#!/usr/bin/env python3
"""Classify a values file for the GitOps image bump (used by gitops-bump.sh).

Prints one of:
  ok              file has a proper `image:` mapping block → safe to edit
  needs-block     file has no image section at all → we may insert one
  skip            nothing to bump (no image/ingress keys — e.g. a helper chart)
  malformed       `image:` exists but in a shape we refuse to rewrite
  missing         file does not exist

`malformed` is deliberately an error rather than a best-effort rewrite: silently
mangling a hand-maintained values file is exactly the failure mode a bump bot
must never have.
"""

from __future__ import annotations

import re
import sys

IMAGE_KEY = re.compile(r"^image:\s*(#.*)?$")
IMAGE_SCALAR = re.compile(r"^image:\s*\S")


def classify(text: str) -> str:
    lines = text.splitlines()
    img_idx = None
    for i, ln in enumerate(lines):
        if IMAGE_KEY.match(ln):
            img_idx = i
            break
    if img_idx is None:
        for ln in lines:
            if IMAGE_SCALAR.match(ln):
                return "malformed"
        if any(re.match(r"^\s*(repository|tag):", ln) for ln in lines):
            return "malformed"
        return "needs-block"
    nxt = None
    for ln in lines[img_idx + 1:]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        nxt = ln
        break
    if nxt is None or (len(nxt) - len(nxt.lstrip(" "))) == 0:
        return "malformed"
    return "ok"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: image_block_state.py <values.yaml>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        print("missing")
        return 0
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(classify(text))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
