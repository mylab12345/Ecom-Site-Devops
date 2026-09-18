#!/usr/bin/env python3
"""Rewrite `image.repository` / `image.tag` (+ optional digest) in every Helm
values file that a service built by scripts/ci/build.sh produced.

Reads the TSV written by build.sh:  <service>\t<image-ref>\t<digest>

This is the GitOps trigger for Phase 5: Jenkins never deploys, it only commits
an image tag, and ArgoCD syncs from git.

    bump_values.py --images-file .ci-output/images.txt [--charts-dir helm-charts]
                   [--digest-mode off|if-present|on] [--report out.md] [--dry-run]

Exit codes: 0 = files written/unchanged · 1 = a values file was refused (malformed)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from image_block_state import classify
from set_yaml_field import set_field

BANNER = "# Managed by scripts/ci/gitops-bump.sh — do not hand-edit the image block."


def read_images(path: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                print(f"warning: skipping unparsable line in {path}: {raw.rstrip()}", file=sys.stderr)
                continue
            svc = parts[0].strip()
            ref = parts[1].strip()
            digest = parts[2].strip() if len(parts) > 2 else "-"
            out.append((svc, ref, digest or "-"))
    return out


def split_ref(ref: str) -> tuple[str, str]:
    """docker.io/hub/ecom-product:1a2b3c → ('docker.io/hub/ecom-product', '1a2b3c')."""
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        return ref, "latest"
    head, tag = ref.rsplit(":", 1)
    return head, tag


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images-file", required=True)
    ap.add_argument("--charts-dir", default="helm-charts")
    ap.add_argument("--digest-mode", choices=("off", "if-present", "on"), default="if-present")
    ap.add_argument("--report", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    images = read_images(args.images_file)
    if not images:
        print(f"error: no images listed in {args.images_file}", file=sys.stderr)
        return 1

    rows: list[str] = []
    rc = 0
    touched = 0

    for svc, ref, digest in images:
        repo, tag = split_ref(ref)
        values = os.path.join(args.charts_dir, svc, "values.yaml")

        if os.path.exists(values):
            with open(values, encoding="utf-8") as fh:
                original = fh.read()
        else:
            # Phase 2 ships stub values files; Phase 4 fills the charts out.
            # Creating the file keeps the bump contract working either way.
            original = ""
            os.makedirs(os.path.dirname(values), exist_ok=True)

        state = classify(original) if original else "needs-block"
        if state == "malformed":
            print(f"error: {values} has an `image:` block this tool will not rewrite — fix it by hand", file=sys.stderr)
            rows.append(f"| {svc} | `{tag}` | refused (malformed image block) |")
            rc = 1
            continue

        text = original
        changed_any = False

        if state == "needs-block":
            # Write the block ourselves so the keys land in canonical order
            # (set_field inserts each new child right below the parent).
            text = text.rstrip("\n")
            if BANNER not in text:
                text = f"{text}\n\n{BANNER}" if text else BANNER
            text = (f"{text}\n\nimage:\n  repository: {repo}\n  tag: \"{tag}\"\n"
                    "  pullPolicy: IfNotPresent\n")
            changed_any = True
        else:
            for key, value, quote in (("image.repository", repo, False), ("image.tag", tag, True)):
                parent, child = key.split(".")
                text, changed = set_field(text, parent, child, value, quote)
                changed_any = changed_any or changed
        if digest and digest != "-" and args.digest_mode in ("on", "if-present"):
            want_digest = args.digest_mode == "on" or re_has_digest(text)
            if want_digest:
                text, changed = set_field(text, "image", "digest", value=digest, quote=True)
                changed_any = changed_any or changed

        if changed_any:
            touched += 1
            rows.append(f"| {svc} | `{tag}` | {'updated' if original else 'created'} |")
            if not args.dry_run:
                with open(values, "w", encoding="utf-8") as fh:
                    fh.write(text if text.endswith("\n") else text + "\n")
        else:
            rows.append(f"| {svc} | `{tag}` | already current |")

    report = "\n".join(
        ["", "### Helm values bump", "", "| service | tag | result |", "|---|---|---|", *rows, ""]
    )
    print(report.rstrip())
    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "a", encoding="utf-8") as fh:
            fh.write(report)
    print(f"{touched} values file(s) {'would change' if args.dry_run else 'changed'}, "
          f"{len(images)} image(s) considered, charts-dir={args.charts_dir}"
          + (" [dry-run]" if args.dry_run else ""))
    return rc


def re_has_digest(text: str) -> bool:
    return "\n  digest:" in text or text.lstrip().startswith("digest:")


if __name__ == "__main__":
    sys.exit(main())
