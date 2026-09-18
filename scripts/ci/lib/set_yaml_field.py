#!/usr/bin/env python3
"""Minimal, comment-preserving YAML field writer for the GitOps bump.

Why not yq/sed? yq is not a guaranteed dependency on a Jenkins agent, and a
sed one-liner on `tag:` rewrites every `tag:` in the file (including the ones
inside `initContainers`, `imagePullSecrets` comments, etc.). This helper is
line-oriented, understands exactly the two-level `parent.child` shape the
pipeline needs, and leaves every other byte (including comments) untouched —
which keeps the bump diff reviewable in the ArgoCD repo.

    set_yaml_field.py --file helm-charts/product/values.yaml \
        --key image.tag --value 1a2b3c4d5e6f [--quote] [--json]

--json prints {"changed": true, "file": ..., "key": ..., "value": ...}.
Exit 0 = written or already up to date · 1 = parse/IO problem.
"""

from __future__ import annotations

import argparse
import json
import re
import sys


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def find_key(lines: list[str], pattern: re.Pattern[str], start: int, parent_indent: int) -> tuple[int, int] | None:
    """First line matching `pattern` inside the parent block, or None once the
    block ends (a line at or above the parent's indentation)."""
    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cur = indent_of(line)
        if cur <= parent_indent:
            return None
        if pattern.match(line):
            return i, cur
    return None


def set_field(text: str, parent: str, child: str, value: str, quote: bool) -> tuple[str, bool]:
    lines = text.splitlines()
    rendered = f'"{value}"' if quote else value
    # Matches `image:` and `image: python:3.12` (scalar form must be promoted).
    parent_re = re.compile(rf"^(\s*){re.escape(parent)}:\s*(\S[^#]*?)?\s*(#.*)?$")
    child_re = re.compile(rf"^(\s+)(?:-\s+)?({re.escape(child)}):\s*(.*?)\s*(#.*)?$")

    p_idx = None
    p_indent = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        m = parent_re.match(line)
        if m and (m.group(2) is not None or m.group(3) is not None or line.rstrip().endswith(":")):
            p_idx, p_indent = i, indent_of(line)
            break

    if p_idx is None:
        # No parent block at all → append a fresh one at the end of the file.
        block = ["", f"{parent}:", f"  {child}: {rendered}"]
        lines.extend(block)
        return "\n".join(lines) + "\n", True

    # `image: python:3.12` scalar form → promote to a mapping.
    scalar = re.match(rf"^(\s*){re.escape(parent)}:\s*(\S.*?)\s*(#.*)?$", lines[p_idx])
    if scalar:
        base_indent = len(scalar.group(1))
        trailing = scalar.group(3) or ""
        new = [f"{' ' * base_indent}{parent}:{trailing}", f"{' ' * (base_indent + 2)}repository: {scalar.group(2)}"]
        lines[p_idx:p_idx + 1] = new
        p_indent = base_indent

    found = find_key(lines, child_re, p_idx + 1, p_indent)
    if found:
        c_idx, c_indent = found
        cur = lines[c_idx]
        comment = ""
        cm = re.search(r"(\s+#.*)$", cur)
        if cm:
            comment = cm.group(1)
        new_line = f"{' ' * c_indent}{child}: {rendered}{comment}"
        if new_line == cur:
            return text, False
        lines[c_idx] = new_line
        return "\n".join(lines) + "\n", True

    # parent block exists but the child key is missing → insert right after it.
    lines.insert(p_idx + 1, f"{' ' * (p_indent + 2)}{child}: {rendered}")
    return "\n".join(lines) + "\n", True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True)
    ap.add_argument("--key", required=True, help="dotted parent.child, e.g. image.tag")
    ap.add_argument("--value", required=True)
    ap.add_argument("--quote", action="store_true", help="emit the value double-quoted")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    if args.key.count(".") != 1:
        print(f"error: --key must be 'parent.child' (got {args.key!r})", file=sys.stderr)
        return 1
    parent, child = args.key.split(".", 1)

    try:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    new_text, changed = set_field(text, parent, child, args.value, args.quote)
    if changed:
        with open(args.file, "w", encoding="utf-8") as fh:
            fh.write(new_text)

    if args.as_json:
        print(json.dumps({"file": args.file, "key": args.key, "value": args.value, "changed": changed}))
    else:
        print(f"{'updated' if changed else 'unchanged'}: {args.file} → {args.key}={args.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
