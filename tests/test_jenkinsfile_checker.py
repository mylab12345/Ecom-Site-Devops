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

from conftest import CHECK_JENKINSFILE, JENKINSFILE_AWS, JENKINSFILE_LOCAL, REPO_ROOT

pytestmark = pytest.mark.structure

# A single-quoted Groovy string, as it appears in `sh '''…'''`. Built at runtime so
# this file can contain it without ending its own string literal.
TQ = "'" * 3

# The two stage contracts the checker enforces, keyed by pipeline file name.
AWS_STAGES = ("Prepare", "Lint", "Unit tests", "Build & push", "Trivy security gate",
              "Promote images", "GitOps bump")
LOCAL_STAGES = ("Prepare", "Lint", "Unit tests", "Build images", "Smoke test",
                "Trivy scan", "Deploy to Kind", "Verify deployment")


def skeleton(step_body: str, stages: tuple[str, ...] = AWS_STAGES) -> str:
    """A complete, valid declarative pipeline with `step_body` spliced into every
    stage — so a test case exercises exactly one rule instead of tripping the
    "missing stage/block" shape checks first."""
    stages = " ".join(f"stage('{name}') {{ steps {{ {step_body} }} }}" for name in stages)
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
    ("a script { } block breaks the declarative-only rule", skeleton("\nscript { echo 1 }\n"), True),
    ("mkdir -p is not a credential", skeleton('sh \'mkdir -p "$OUT"\''), False),
]


def run_checker(source: str, tmp_path, name: str = "Jenkinsfile.aws"):
    """The checker picks its stage contract from the file name, so the name matters."""
    target = tmp_path / name
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


@pytest.mark.parametrize("pipeline", [JENKINSFILE_LOCAL, JENKINSFILE_AWS], ids=lambda p: p.name)
def test_the_shipped_pipelines_pass_their_own_lint(pipeline):
    """If a pipeline we ship does not satisfy the lint we wrote for it, the gate is
    theatre."""
    assert pipeline.is_file(), f"{pipeline.name} missing — both pipelines are deliverables"
    proc = subprocess.run([sys.executable, str(CHECK_JENKINSFILE), str(pipeline)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{pipeline.name} failed the structure check:\n{proc.stdout}\n{proc.stderr}"


def test_the_local_stage_contract_is_enforced(tmp_path):
    proc = run_checker(skeleton("echo 'ok'", stages=LOCAL_STAGES), tmp_path, name="Jenkinsfile.local")
    assert proc.returncode == 0, proc.stdout


def test_a_renamed_local_stage_is_reported(tmp_path):
    """`Deploy to Kind` is what makes the local pipeline a deploy pipeline: if the
    stage is renamed, the JUnit globs and README §5 silently stop describing it."""
    stages = tuple(s for s in LOCAL_STAGES if s != "Deploy to Kind")
    proc = run_checker(skeleton("echo 'ok'", stages=stages), tmp_path, name="Jenkinsfile.local")
    assert proc.returncode != 0
    assert "Deploy to Kind" in proc.stdout


def test_the_aws_pipeline_may_not_deploy(tmp_path):
    proc = run_checker(skeleton("sh 'kubectl -n ecom get pods'"), tmp_path)
    assert proc.returncode != 0
    assert "must not deploy" in proc.stdout


def test_required_stage_list_matches_the_documented_flow():
    """README §5 and jenkins/setup.md §1 both describe these stage lists; the checker's
    PIPELINE_STAGES must not drift from what the docs promise."""
    checker = CHECK_JENKINSFILE.read_text(encoding="utf-8")
    for name in (*AWS_STAGES, *LOCAL_STAGES):
        assert f"stage('{name}')" in checker, f"checker no longer requires the '{name}' stage"
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("Build & push", "Trivy security gate", "GitOps bump", "Deploy to Kind", "Verify deployment"):
        assert name in readme, f"README §5 no longer documents the '{name}' stage"


def test_both_shipped_pipelines_are_named_in_the_docs():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("Jenkinsfile.local", "Jenkinsfile.aws"):
        assert name in readme, f"README §5 must tell a reader which pipeline to point Jenkins at ({name})"
