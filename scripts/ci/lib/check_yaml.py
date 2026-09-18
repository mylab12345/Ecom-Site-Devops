#!/usr/bin/env python3
"""Dependency-free YAML gate used by scripts/ci/lint.sh.

Parses every first-party YAML in the repo with PyYAML when it is available and
exits 0 with `PYYAML_MISSING` when it is not (so a laptop without the dev
requirements still gets a green lint run instead of a false failure).

Go-templated files (Helm `templates/*.yaml`) are skipped on purpose: they are
not valid YAML until Helm renders them, and `helm template` (Phase 4) owns that
check.
"""

from __future__ import annotations

import glob
import os
import sys


def candidates(root: str) -> list[str]:
    files = [os.path.join(root, "docker-compose.yaml"), os.path.join(root, ".hadolint.yaml")]
    for pattern in (
        "helm-charts/*/*.yaml",
        "helm-charts/*/*.yml",
        "argocd/*.yaml",
        "argocd/applications/*.yaml",
        "observability/*/*.yaml",
        "jenkins/**/*.yaml",
    ):
        files.extend(sorted(glob.glob(os.path.join(root, pattern), recursive=True)))
    keep: list[str] = []
    for f in files:
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                body = fh.read()
        except OSError:
            continue
        if "{{" in body or "${" in body:  # templated: not static YAML
            continue
        keep.append(f)
    return keep


def main() -> int:
    root = os.environ.get("ECOM_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    try:
        import yaml  # type: ignore
    except ImportError:
        print("PYYAML_MISSING")
        return 0

    files = candidates(root)
    bad: list[str] = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                docs = list(yaml.safe_load_all(fh))
        except Exception as exc:  # a broken YAML doc is the finding itself
            bad.append(f"{os.path.relpath(f, root)}: {exc}")
            continue
        for i, d in enumerate(docs):
            if d is None and len(docs) > 1:
                bad.append(f"{os.path.relpath(f, root)}: document {i} is empty")
    if bad:
        print("\n".join(bad))
        return 1
    print(f"OK {len(files)} yaml file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
