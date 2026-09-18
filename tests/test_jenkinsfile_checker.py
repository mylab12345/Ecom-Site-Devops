"""The Jenkinsfile checker, checked.

`scripts/ci/lib/check_jenkinsfile.py` exists because Jenkins is the only thing that
parses a Jenkinsfile — so you normally discover an unbalanced brace, or a string
Groovy refuses to interpolate, by running a build. A lint that never fails is worse
than no lint, so every rule here is asserted with a snippet that must fire and one
that must stay quiet.

Structure tier: stdlib + subprocess only.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from conftest import CHECK_JENKINSFILE, JENKINSFILE, REPO_ROOT

pytestmark = pytest.mark.structure

# A single-quoted Groovy string, as it appears in `sh '''…'''`. Built at runtime so
# this file can contain it without ending its own string literal.
TQ = "'" * 3

STAGE_NAMES = ("Prepare", "Lint", "Unit tests", "Build & push", "Trivy security gate",
               "Promote images", "GitOps bump")


def skeleton(step_body: str) -> str:
    """A complete, valid declarative pipeline with `step_body` spliced into every
    stage — so a test case exercises exactly one rule instead of tripping the
    "missing stage/block" shape checks first."""
    stages = " ".join(f"stage('{name}') {{ steps {{ {step_body} }} }}" for name in STAGE_NAMES)
    return (
        "pipeline { agent { label 'x' } options { timeout(time: 1, unit: 'MINUTES') } "
        "parameters { string(name: 'A', defaultValue: '', description: '') } "
        "environment { A = '1' } stages { " + stages + " } post { always { echo 'x' } } }"
    )


GOOD = skeleton("echo 'ok'")

CASES = [
    ("a complete minimal pipeline is accepted", GOOD, False),
    ("unbalanced brace", GOOD[:-1], True),
    ("GString brace closed inside a shell block", "pipeline { stages { } } steps { } } }", True),
    ("${params.X} written inside a single-quoted sh block never interpolates",
     skeleton(f"sh {TQ}echo ${{params.FOO}}{TQ}"), True),
    ("the same value interpolated by Groovy is fine",
     skeleton('sh "echo ${params.FOO}"'), False),
    ("token interpolated by Groovy into a command", skeleton('sh "echo $GITHUB_TOKEN"'), True),
    ("token bound via withCredentials and read by the shell",
     skeleton("withCredentials([usernameVariable: 'U']) { sh 'echo $U' }"), False),
    ("helper method declared after the pipeline block", GOOD + "\ndef foo() { return 1 }", True),
    ("literal password argument on docker login",
     skeleton("sh 'docker login -u me -p hunter2'"), True),
    ("password on stdin is the sanctioned form",
     skeleton('sh \'printf %s "$PASS" | docker login --password-stdin\''), False),
    ("mkdir -p is not a credential", skeleton('sh \'mkdir -p "$OUT"\''), False),
]


def run_checker(source: str, tmp_path):
    target = tmp_path / "Jenkinsfile"
    target.write_text(source, encoding="utf-8")
    return subprocess.run([sys.executable, str(CHECK_JENKINSFILE), str(target)],
                          capture_output=True, text=True, check=False)


def test_checker_script_exists():
    assert CHECK_JENKINSFILE.is_file(), "scripts/ci/lib/check_jenkinsfile.py missing (lint.sh calls it)"


@pytest.mark.parametrize(("kind", "source", "expect_finding"), CASES, ids=[c[0] for c in CASES])
def test_checker_rules_fire_as_documented(kind, source, expect_finding, tmp_path):
    proc = run_checker(source, tmp_path)
    fired = proc.returncode != 0
    assert fired is expect_finding, (
        f"rule “{kind}” did not {'fire' if expect_finding else 'stay quiet'} (rc={proc.returncode}):\n"
        f"{proc.stdout.strip()}\n{proc.stderr.strip()}"
    )


@pytest.mark.skipif(not JENKINSFILE.exists(), reason="Jenkinsfile not written yet")
def test_the_shipped_jenkinsfile_passes_its_own_lint():
    """If the pipeline we ship does not satisfy the lint we wrote for it, the gate is
    theatre."""
    proc = subprocess.run([sys.executable, str(CHECK_JENKINSFILE), str(JENKINSFILE)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Jenkinsfile failed the structure check:\n{proc.stdout}\n{proc.stderr}"


def test_required_stage_list_matches_the_documented_flow():
    """README §4.5 and jenkins/setup.md §1 both describe this stage list; the checker's
    REQUIRED_STAGES must not drift from what the docs promise."""
    checker = CHECK_JENKINSFILE.read_text(encoding="utf-8")
    for name in ("Prepare", "Lint", "Unit tests", "Build & push", "Trivy security gate",
                 "Promote images", "GitOps bump"):
        assert f"stage('{name}')" in checker, f"checker no longer requires the '{name}' stage"
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("Build & push", "Trivy security gate", "GitOps bump"):
        assert name in readme, f"README §4.5 no longer documents the '{name}' stage"
