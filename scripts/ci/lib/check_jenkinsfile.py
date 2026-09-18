#!/usr/bin/env python3
"""Jenkinsfile sanity checker (stdlib, no JVM, no Jenkins).

A pipeline cannot be linted by Jenkins until a build actually runs, which is a slow
feedback loop for a Groovy file. This catches the mistakes that account for most of
the pain, in both `Jenkinsfile.local` and `Jenkinsfile.aws`:

  1. unbalanced { } [ ] ( )  — including inside strings/comments, where they must not count
  2. a `${params.x}` / `${env.x}` written inside a *single*-quoted Groovy string, where
     Groovy does not interpolate and the shell receives the literal text (silently empty)
  3. a declarative stage name that a `when { beforeAgent }` or a report glob depends on
     disappearing or being renamed (the stage list is per pipeline, see PIPELINE_STAGES)
  4. Groovy `def`/helper methods declared outside the `pipeline { }` block, which the
     declarative parser rejects with a confusing error
  5. credential material interpolated by Groovy, or handed to a command as a literal
  6. the cloud pipeline reaching for kubectl/helm/terraform — ArgoCD owns that cluster
  7. a `script { }` block — these pipelines are declarative only

Exit 0 = looks sane · 1 = findings. Line numbers are reported for every finding.
"""

from __future__ import annotations

import os
import re
import sys

OPEN = {"{": "}", "[": "]", "(": ")"}
CLOSE = {v: k for k, v in OPEN.items()}

REQUIRED_BLOCKS = ("pipeline {", "agent ", "stages {", "post {", "environment {", "parameters {", "options {")

# There are two pipelines, and they are allowed to be different: the local one
# deploys to Kind, the AWS one hands over to ArgoCD and must never touch a
# cluster. Anything not listed here only has to lint and test.
PIPELINE_STAGES = {
    "jenkinsfile.local": ("stage('Prepare')", "stage('Lint')", "stage('Unit tests')",
                          "stage('Build images')", "stage('Smoke test')", "stage('Trivy scan')",
                          "stage('Deploy to Kind')", "stage('Verify deployment')"),
    "jenkinsfile.aws": ("stage('Prepare')", "stage('Lint')", "stage('Unit tests')",
                        "stage('Build & push')", "stage('Trivy security gate')",
                        "stage('Promote images')", "stage('GitOps bump')"),
}
COMMON_STAGES = ("stage('Lint')", "stage('Unit tests')")
# Deploy verbs that have no business appearing in the cloud pipeline (rule 6).
CLUSTER_VERBS = re.compile(r"\bkubectl\b|helm\s+(?:upgrade|install|rollback)|terraform\s+\S*\s*apply")


def stages_for(path: str) -> tuple[str, ...]:
    """The stage contract for this pipeline, chosen by file name."""
    return PIPELINE_STAGES.get(os.path.basename(path).lower(), COMMON_STAGES)


def scan(text: str):
    """Yield (kind, line, snippet) for regions worth checking, char-exact.

    Emulates just enough Groovy lexing to know when we are inside a string:
    triple-quoted, single-quoted, double-quoted, line/block comments.
    """
    i, line = 0, 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        # line comment
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            yield ("comment", line, text[i:j])
            i = j
            continue
        # block comment
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            yield ("block-comment", line, text[i:j])
            line += text.count("\n", i, j)
            i = j
            continue
        # triple-quoted (Groovy does not interpolate in ''')
        if text.startswith("'''", i):
            j = text.find("'''", i + 3)
            j = n if j == -1 else j
            body = text[i + 3:j]
            yield ("single-triple", line, body)
            line += body.count("\n")
            i = j + 3 if j + 3 <= n else n
            continue
        if text.startswith('"""', i):
            j = text.find('"""', i + 3)
            j = n if j == -1 else j
            body = text[i + 3:j]
            yield ("double-triple", line, body)
            line += body.count("\n")
            i = j + 3 if j + 3 <= n else n
            continue
        if c in "\"'":
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "\n":       # unterminated single-line string
                    break
                j += 1
            yield ("single" if quote == "'" else "double", line, text[i + 1:j])
            i = j + 1
            continue
        yield ("code", line, c)
        i += 1


def check(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    findings: list[str] = []

    # 1) bracket balance, ignoring string/comment interiors
    stack: list[tuple[str, int]] = []
    for kind, line, body in scan(text):
        if kind in ("comment", "block-comment"):
            continue
        if kind in ("single", "double", "single-triple", "double-triple"):
            # Braces inside a shell heredoc-ish string are the shell's business,
            # except that Groovy GString ${...} must still balance.
            depth = 0
            for m in re.finditer(r"[{}]", body):
                depth += 1 if m.group(0) == "{" else -1
                if depth < 0:
                    findings.append(f"{path}:{line}: unbalanced '}}' inside a {kind} string")
                    break
            if depth:
                findings.append(f"{path}:{line}: {kind} string has {depth:+d} unbalanced GString braces")
            continue
        for ch in body:
            if ch in OPEN:
                stack.append((ch, line))
            elif ch in CLOSE:
                if not stack:
                    findings.append(f"{path}:{line}: stray '{ch}' with no opener")
                else:
                    opener, oline = stack.pop()
                    if OPEN[opener] != ch:
                        findings.append(f"{path}:{line}: '{ch}' closes '{opener}' opened at line {oline}")
    for opener, oline in stack:
        findings.append(f"{path}:{oline}: '{opener}' never closed")

    # 2) ${params.x} / ${env.x} inside single-quoted strings (Groovy won't interpolate)
    for kind, line, body in scan(text):
        if kind in ("single", "single-triple"):
            for m in re.finditer(r"\$\{(params|env)\.[A-Za-z_][\w.]*\}", body):
                findings.append(
                    f"{path}:{line}: {m.group(0)} sits in a single-quoted Groovy string and will "
                    f"reach the shell literally (empty) — interpolate with \"...\" or read the env var directly"
                )

    # 3) structure that the declarative parser and our JUnit globs rely on
    for block in REQUIRED_BLOCKS:
        if block not in text:
            findings.append(f"{path}:0: missing `{block}` block (declarative pipeline shape)")
    for stage in stages_for(path):
        if stage not in text:
            findings.append(f"{path}:0: missing {stage} — README §5 and the JUnit globs name it")

    # 4) top-level Groovy methods outside pipeline { }
    head = text.split("pipeline {", 1)
    if len(head) == 2 and re.search(r"^\s*(?:private\s+|public\s+)?def\s+\w+\s*\(", head[1], re.M):
        findings.append(f"{path}:0: `def method()` after the pipeline block — the declarative parser "
                        f"rejects it (move the logic into a `script {{ }}` stage or a shared library)")

    # 5) credential material must never be Groovy-interpolated into a command.
    #    "$GIT_TOKEN" inside a single-quoted sh block is fine (the shell expands it and
    #    Jenkins masks it); ${GIT_TOKEN} inside a Groovy string is not — it is baked
    #    into the program XML and one forgotten `echo` away from the build log.
    for kind, line, body in scan(text):
        if kind in ("double", "double-triple"):
            for m in re.finditer(r"\$\{?\w*(SECRET|TOKEN|PASSWORD|PASSWD)\w*\}?", body):
                findings.append(f"{path}:{line}: {m.group(0)} interpolated by Groovy — bind it with "
                                f"withCredentials and reference it as a shell variable instead")
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw.strip().startswith(("#", "//", "*")):
            continue
        # Scope this to what it actually means: a secret handed to `docker login`
        # inline, or assigned as a literal. `mkdir -p` is not a credential.
        inline_login_secret = re.search(r"docker\s+login\b[^\n]*\s-p[ =]\S", raw)
        literal_password = re.search(r"password\s*=\s*[\"']?\S", raw, re.I) and "passwordVariable" not in raw
        if (inline_login_secret or literal_password) and "--password-stdin" not in raw:
            findings.append(f"{path}:{lineno}: credential passed as a literal argument — "
                            f"pipe it into `--password-stdin` (a token in argv is readable via `ps` "
                            f"and lands in the build log)")

    # 6) the cloud pipeline hands over to ArgoCD; it must not deploy by itself.
    #    A build box with cluster credentials is a much bigger blast radius than a
    #    build box with a registry login, and "CI never deploys" is only true if
    #    something fails the build when CI tries.
    if os.path.basename(path).lower() == "jenkinsfile.aws":
        for lineno, raw in enumerate(text.splitlines(), 1):
            if raw.strip().startswith(("//", "*", "/*")):
                continue
            if CLUSTER_VERBS.search(raw):
                findings.append(f"{path}:{lineno}: the AWS pipeline must not deploy — ArgoCD "
                                f"reconciles from git after the GitOps bump (drop the "
                                f"kubectl/helm/terraform call, or move it to Jenkinsfile.local)")

    # 7) declarative only. A `script { }` block is where a pipeline stops being
    #    readable — and everything these two need (values, conditions, credentials)
    #    has a declarative form. Logic that really is needed belongs in scripts/ci/.
    for m in re.finditer(r"^\s*script\s*\{", text, re.M):
        lineno = text.count("\n", 0, m.start()) + 1
        findings.append(f"{path}:{lineno}: `script {{ }}` block — keep the pipeline declarative "
                        f"(environment/when/withCredentials cover it, or move the logic into "
                        f"scripts/ci/ or the Makefile)")
    return findings


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_jenkinsfile.py <Jenkinsfile> [...]", file=sys.stderr)
        return 2
    bad = 0
    for path in argv:
        found = check(path)
        if found:
            bad = 1
            print("\n".join(found))
        else:
            print(f"OK {path}: balanced delimiters, required stages present, no string-interpolation traps")
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
