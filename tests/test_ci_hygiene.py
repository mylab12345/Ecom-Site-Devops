"""Structure tier (marker `structure`): the CI ↔ repository contracts.

Stdlib only — this is what `scripts/ci/lint.sh` runs on a bare agent before
anything is installed, in about a second. Every test guards something the Phase
2 pipeline really depends on, so a future "harmless" edit (a new service, a
renamed port, a reformatted Dockerfile) fails in CI instead of at 02:00 in the
cluster.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys

import pytest

from conftest import (
    CHARTS_DIR,
    CI_DIR,
    COMPOSE_FILE,
    GATEWAY_CONFIG,
    INFRA_SERVICES,
    REPO_ROOT,
    REGISTRY,
    REGISTRY_FILE,
    SERVICES_DIR,
    SERVICE_NAMES,
    parse_compose_ports,
    parse_compose_services,
    port_of,
)

pytestmark = pytest.mark.structure


def compose_text() -> str:
    return COMPOSE_FILE.read_text(encoding="utf-8")


# ── the registry itself ─────────────────────────────────────────────────────
def test_registry_has_ten_services_with_unique_ports():
    assert len(REGISTRY) == 10, f"expected 10 services, got {len(REGISTRY)}: {SERVICE_NAMES}"
    assert len(set(SERVICE_NAMES)) == 10
    ports = [port_of(name) for name in SERVICE_NAMES]
    assert len(set(ports)) == 10, "two services share a port"
    assert sorted(ports) == sorted([*range(8001, 8010), 8080])


def test_registry_tiers_are_declared():
    tiers = {entry["tier"] for entry in REGISTRY}
    assert tiers <= {"stateless", "critical", "edge"}, tiers
    # Phase 3's spot-vs-on-demand node placement keys off this split.
    assert {"stateless", "critical"} <= tiers


def test_registry_covers_every_service_directory():
    on_disk = {p.name for p in SERVICES_DIR.iterdir() if (p / "Dockerfile").exists()}
    assert on_disk == set(SERVICE_NAMES), (
        f"services/ and scripts/ci/lib/services.sh disagree — not in registry: {sorted(on_disk - set(SERVICE_NAMES))}, "
        f"registry without dir: {sorted(set(SERVICE_NAMES) - on_disk)}"
    )


def test_registry_file_is_the_single_source_of_truth():
    """No script or pipeline may keep its own copy of the service list."""
    offenders = []
    for script in (*sorted(CI_DIR.glob("*.sh")), REPO_ROOT / "Jenkinsfile"):
        if not script.exists():
            continue
        text = script.read_text(encoding="utf-8")
        if re.search(r"identity['\"].{0,220}gateway['\"]", text, re.S):
            offenders.append(script.name)
        if re.search(r"['\"]identity['\"],\s*['\"]product['\"],\s*['\"]inventory['\"]", text):
            offenders.append(script.name)
    assert not offenders, f"forked service list in {sorted(set(offenders))} — source scripts/ci/lib/services.sh"


# ── compose parity ───────────────────────────────────────────────────────────
def test_compose_declares_registry_plus_infra():
    services = parse_compose_services(compose_text())
    assert set(services) == {*SERVICE_NAMES, *INFRA_SERVICES}, (
        "docker-compose.yaml services must equal the CI registry plus the infra trio"
    )


def test_compose_port_mappings_match_registry_ports():
    ports = parse_compose_ports(compose_text())
    for name in SERVICE_NAMES:
        port = port_of(name)
        mapping = ports.get(name) or []
        assert f"{port}:{port}" in mapping, f"compose service '{name}' should publish {port}:{port}, got {mapping}"


@pytest.mark.parametrize("name", SERVICE_NAMES)
def test_compose_service_is_production_shaped(name):
    declared = set(parse_compose_services(compose_text())[name])
    for required in ("healthcheck", "depends_on", "networks", "restart"):
        assert required in declared, f"compose {name}: missing '{required}'"


def test_compose_infra_has_init_and_healthchecks():
    services = parse_compose_services(compose_text())
    for infra in INFRA_SERVICES:
        assert "healthcheck" in services[infra], f"{infra} needs a healthcheck: services gate depends_on it"
    assert "init-db.sql" in compose_text(), "postgres must mount scripts/init-db.sql to create the 9 DBs"


# ── gateway routing table parity ─────────────────────────────────────────────
def test_gateway_service_map_matches_registry():
    text = GATEWAY_CONFIG.read_text(encoding="utf-8")
    found = re.findall(r'"([a-z]+)":\s*"http://([a-z]+):(\d{4})"', text)
    assert found, "could not parse the gateway service map"
    pairs = {name: (target, int(port)) for name, target, port in found}
    assert set(pairs) == set(SERVICE_NAMES) - {"gateway"}, "the gateway map must cover the 9 upstreams"
    for name in set(SERVICE_NAMES) - {"gateway"}:
        target, target_port = pairs[name]
        assert target == name, f"gateway maps {name} → {target}"
        assert target_port == port_of(name), f"gateway port for {name} is {target_port}, registry says {port_of(name)}"


def test_gateway_routes_cover_every_registry_prefix():
    gw = (SERVICES_DIR / "gateway" / "app" / "main.py").read_text(encoding="utf-8")
    routes = dict(re.findall(r'"(/api/[a-z]+)":\s*"([a-z]+)"', gw))
    assert routes, "could not parse ROUTES in the gateway"
    for entry in REGISTRY:
        prefix = entry["prefix"]
        if prefix == "/":
            continue
        assert prefix in routes, f"gateway has no route for {prefix} ({entry['name']})"
        assert routes[prefix] == entry["name"], f"{prefix} routes to {routes[prefix]}, expected {entry['name']}"


# ── Dockerfile contract (build.sh + Phase 3 multi-arch depend on it) ────────
@pytest.mark.parametrize("name", SERVICE_NAMES)
def test_dockerfile_contract(name):
    df = (SERVICES_DIR / name / "Dockerfile").read_text(encoding="utf-8")
    port = port_of(name)

    assert df.startswith("# syntax=docker/dockerfile:1"), f"{name}: missing BuildKit syntax directive"
    base = re.search(r"^FROM\s+(\S+)", df, re.M)
    assert base, f"{name}: no FROM line"
    assert base.group(1).startswith("python:3.12"), (
        f"{name}: base must stay python:3.12-slim — it is what makes the ARM64 build wheel-clean "
        f"(got {base.group(1)})"
    )
    assert f"EXPOSE {port}" in df, f"{name}: EXPOSE {port} missing"
    assert f'--port","{port}"' in df.replace(" ", ""), f"{name}: uvicorn must bind {port}"
    assert "useradd" in df, f"{name}: must create a dedicated uid"
    assert "USER appuser" in df, f"{name}: must drop to the non-root user"
    assert "HEALTHCHECK" in df, f"{name}: HEALTHCHECK required"
    assert "/health" in df, f"{name}: HEALTHCHECK must target /health (the path K8s probes)"
    assert "--no-install-recommends" in df, f"{name}: apt layer should use --no-install-recommends"
    assert "rm -rf /var/lib/apt/lists/*" in df, f"{name}: apt lists must be cleaned in the same layer"
    # either form is fine, but one of them is mandatory: a warm pip cache triples every layer
    assert "PIP_NO_CACHE_DIR=1" in df or "pip install --no-cache-dir" in df, f"{name}: pip cache bloats every layer"


@pytest.mark.parametrize("name", SERVICE_NAMES)
def test_service_has_dockerignore(name):
    path = SERVICES_DIR / name / ".dockerignore"
    assert path.exists(), (
        f"{name}: .dockerignore missing — the build context must not ship __pycache__ "
        "(it busts the CI layer cache on every run)"
    )
    text = path.read_text(encoding="utf-8")
    for token in ("__pycache__", "*.pyc", ".env"):
        assert token in text, f"{name}/.dockerignore should exclude {token}"


@pytest.mark.parametrize("name", SERVICE_NAMES)
def test_requirements_are_fully_pinned(name):
    """Unpinned deps make rebuilds non-reproducible: the image you scanned is not
    the image you deploy, and Trivy findings start moving between builds."""
    lines = (SERVICES_DIR / name / "requirements.txt").read_text(encoding="utf-8").splitlines()
    unpinned = [
        ln.strip() for ln in lines
        if ln.strip() and not ln.strip().startswith("#")
        and not re.match(r"^[A-Za-z0-9_.[\]-]+==[A-Za-z0-9_.+ \[\]-]+$", ln.strip())
    ]
    assert not unpinned, f"{name}: unpinned requirement lines {unpinned}"


# ── the CI scripts themselves ───────────────────────────────────────────────
CI_SCRIPTS = ["lint.sh", "unit-tests.sh", "build.sh", "scan.sh", "smoke.sh", "gitops-bump.sh", "all.sh"]


@pytest.mark.parametrize("script", CI_SCRIPTS)
def test_ci_script_exists_parses_and_is_executable(script):
    path = CI_DIR / script
    assert path.is_file(), f"scripts/ci/{script} missing (Phase 2 deliverable)"
    assert path.stat().st_mode & stat.S_IXUSR, f"{script} is not executable — Jenkins calls it directly"
    proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{script} is not valid bash:\n{proc.stderr}"


@pytest.mark.parametrize("script", ["build.sh", "scan.sh", "smoke.sh"])
def test_ci_scripts_share_the_registry(script):
    text = (CI_DIR / script).read_text(encoding="utf-8")
    assert "lib/services.sh" in text, f"{script} must source the service registry, not restate it"


def test_registry_lib_is_self_documenting():
    assert "ECOM_CI_SERVICES_RAW" in REGISTRY_FILE.read_text(encoding="utf-8")


def test_ci_helpers_compile():
    for helper in sorted((CI_DIR / "lib").glob("*.py")):
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(helper)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"{helper.name}: {proc.stderr}"


def test_trivy_and_hadolint_configs_present():
    for cfg in (".trivyignore", ".hadolint.yaml"):
        assert (REPO_ROOT / cfg).is_file(), f"{cfg} missing — the gates need their tuning files in-repo"
    ignore = (REPO_ROOT / ".trivyignore").read_text(encoding="utf-8")
    rules = [ln.strip() for ln in ignore.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert len(rules) <= 40, "the CVE allowlist is growing unreviewed — keep it short and justified"
    for line in rules:
        assert re.match(r"^(CVE-\d{4}-\d+|GHSA-[a-z0-9-]+|RHSA-\d{4}:\d+|DLH-[A-Z0-9]+)$", line), (
            f".trivyignore entries must be advisory IDs, not package globs (found {line!r})"
        )


# ── Helm values: the bump target must exist and be shaped right ────────────
@pytest.mark.parametrize("name", SERVICE_NAMES)
def test_helm_values_stub_declares_the_image_contract(name):
    values = CHARTS_DIR / name / "values.yaml"
    assert values.is_file(), f"helm-charts/{name}/values.yaml missing — gitops-bump.sh has nothing to bump"
    text = values.read_text(encoding="utf-8")
    assert re.search(r"^image:\s*$", text, re.M), f"{name}/values.yaml needs a top-level `image:` mapping block"
    assert re.search(r"^  repository: \S+", text, re.M), f"{name}/values.yaml: image.repository missing"
    assert re.search(r'^  tag: ".+"', text, re.M), (
        f"{name}/values.yaml: image.tag must be quoted — YAML reads an unquoted 0123 as a number"
    )
    assert f"port: {port_of(name)}" in text, f"{name}/values.yaml service.port must match the registry port"
    assert "\t" not in text, f"{name}/values.yaml must not contain tabs"


def test_helm_values_placeholders_for_later_phases():
    """Phase 4 owns the chart bodies; Phase 2 only leaves explicit markers so the
    phase boundary is visible instead of implied."""
    for placeholder in ("ecom-common", "ingress-nginx", "network-policies"):
        assert (CHARTS_DIR / placeholder).is_dir(), f"helm-charts/{placeholder} placeholder missing"


# ── Jenkinsfile ↔ scripts contract ─────────────────────────────────────────
JENKINSFILE = REPO_ROOT / "Jenkinsfile"
REQUIRED_STAGES = ["Prepare", "Lint", "Unit tests", "Build & push", "Trivy security gate",
                   "Promote images", "GitOps bump"]
jenkinsfile_or_skip = pytest.mark.skipif(not JENKINSFILE.exists(), reason="Jenkinsfile not written yet")


@jenkinsfile_or_skip
def test_jenkinsfile_delegates_to_the_ci_scripts():
    text = JENKINSFILE.read_text(encoding="utf-8")
    for script in ("scripts/ci/lint.sh", "scripts/ci/unit-tests.sh", "scripts/ci/build.sh",
                   "scripts/ci/scan.sh", "scripts/ci/gitops-bump.sh"):
        assert script in text, f"Jenkinsfile must call {script} — laptop/CI parity is the whole point"
    assert not re.search(r"docker\s+buildx\s+build\b", text), (
        "Jenkinsfile must not inline a `docker buildx build`; that lives in scripts/ci/build.sh"
    )


@jenkinsfile_or_skip
@pytest.mark.parametrize("stage", REQUIRED_STAGES)
def test_jenkinsfile_declares_every_phase2_stage(stage):
    text = JENKINSFILE.read_text(encoding="utf-8")
    assert f"stage('{stage}')" in text, f"Jenkinsfile is missing stage('{stage}')"


@jenkinsfile_or_skip
def test_jenkinsfile_is_not_the_phase1_placeholder():
    text = JENKINSFILE.read_text(encoding="utf-8")
    assert "placeholder" not in text, "Jenkinsfile is still the Phase 1 scaffold"
    assert "echo 'trivy scan'" not in text
    for block in ("agent", "options", "environment", "parameters", "post"):
        assert re.search(rf"\b{block}\s*\{{", text), f"Jenkinsfile should declare a {block} block"


@jenkinsfile_or_skip
def test_jenkinsfile_publishes_reports_and_cleans_up():
    text = JENKINSFILE.read_text(encoding="utf-8")
    assert "junit " in text or "junit(" in text, "publish JUnit results from the test + trivy stages"
    assert "archiveArtifacts" in text, "build reports must survive the workspace wipe"
    assert "cleanWs" in text, "wipe the workspace so a stale tag cannot leak into the next build"
    assert "docker login" in text, "registry creds must be scoped to the build"
    assert "docker logout" in text, "…and removed again: a lingering auths file outlives the build"
    assert "timeout(" in text, "agents hang; a pipeline without a timeout parks an executor forever"
    assert "retry(" in text, "registries flake; retry belongs on the push, not on the whole build"


@jenkinsfile_or_skip
def test_jenkinsfile_gitops_stage_is_branch_guarded():
    text = JENKINSFILE.read_text(encoding="utf-8")
    assert "GITOPS_ENABLED" in text, "the bump must be toggleable"
    assert re.search(r"BRANCH_NAME|env\.GIT_BRANCH", text), "the bump must be limited to the trunk branch"


# ── docs & configuration surface ────────────────────────────────────────────
def test_readme_documents_phase2():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Phase 2 ✅" in text, "README should mark Phase 2 complete"
    for token in ("scripts/ci/build.sh", "Jenkinsfile", "trivy", "linux/arm64"):
        assert token in text, f"README §Phase 2 must mention {token}"


def test_jenkins_docs_present_and_substantive():
    setup = REPO_ROOT / "jenkins" / "setup.md"
    assert setup.is_file(), "jenkins/setup.md is a Phase 2 deliverable"
    text = setup.read_text(encoding="utf-8")
    assert len(text.splitlines()) > 150, "jenkins/setup.md looks unfinished"
    for token in ("docker-buildx", "dockerhub-creds", "gitops-token", "github-webhook", "binfmt", "JCasC"):
        assert token in text, f"jenkins/setup.md must document {token}"


def test_env_example_covers_ci_settings():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("DOCKERHUB_USERNAME", "IMAGE_TAG", "REGISTRY", "PLATFORMS", "TRIVY_SEVERITY", "GITOPS_BRANCH"):
        assert re.search(rf"^{key}=", text, re.M), f".env.example should document {key} for CI"


def test_makefile_exposes_ci_targets():
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("ci:", "ci-lint:", "ci-test:", "ci-build:", "ci-scan:", "ci-smoke:", "gitops-bump:", "ci-deps:"):
        assert re.search(rf"^{re.escape(target)}", text, re.M), f"Makefile target '{target}' missing"
    # .PHONY is wrapped with backslash continuations, so join them first.
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    phony = re.search(r"^\.PHONY:(.*)$", joined, re.M)
    assert phony is not None, "Makefile must declare .PHONY"
    assert "ci-lint" in phony.group(1), ".PHONY must list the ci targets (else a file named 'ci-lint' silently skips the gate)"


def test_gitops_bump_recovers_from_a_rejected_push():
    """`values.yaml` has one writer per job, but not one writer per branch. When the
    push is rejected the script must replay the bump (our rewrite of image.tag is
    deterministic, so rebasing is safe) and must NOT auto-resolve a conflict on it:
    two builds disagreeing about a tag is the one outcome a human has to see.

    The identity assertions are not pedantry — `git commit` in this script carries
    `-c user.*` because agents have no global identity, and a `rebase` that forgets it
    dies with "empty ident name" in the recovery path, i.e. only when two builds raced.
    """
    text = (CI_DIR / "gitops-bump.sh").read_text(encoding="utf-8")
    assert "git push" in text, "bump must push HEAD:refs/heads/$GITOPS_BRANCH"
    assert 'git fetch -q "$REMOTE" "$BRANCH"' in text, "a rejected push must first fetch the moved branch"
    assert 'git "${GIT_ID[@]}" rebase -q --autostash FETCH_HEAD' in text, (
        "…then replay the bump on top of it, with the bot identity, and --autostash because "
        "`rebase` refuses to run over an agent workspace that is not perfectly clean"
    )
    assert "git rebase --abort" in text, "a conflict must abort, not leave the agent mid-rebase for the next build"
    assert "refusing to guess" in text, "the conflict message must name the decision a human owns"
    # the recovery path runs on an agent whose workspace now holds an unpushed commit
    assert text.count("restore_workspace_after_failure") >= 3, (
        "commit failure, push failure and the success tail must all put the workspace back"
    )

def test_gitignore_excludes_ci_artifacts_and_keeps_dockerignore():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".ci-output/" in text, "CI artifacts must be ignored"
    assert ".pytest_cache/" in text
    assert re.search(r"^\.dockerignore$", text, re.M) is None, (
        ".gitignore must NOT ignore .dockerignore — those files belong in git (Phase 1 leftover)"
    )


@pytest.mark.parametrize("name", SERVICE_NAMES)
def test_no_pycache_committed_in_build_context(name):
    junk = [p.name for p in (SERVICES_DIR / name).rglob("__pycache__")]
    assert not junk, f"{name}: __pycache__ inside the build context"


def test_ci_scripts_have_no_dangling_helper_references():
    missing = []
    for script in sorted(CI_DIR.glob("*.sh")):
        text = script.read_text(encoding="utf-8")
        for rel in re.findall(r'source "\$ECI_LIB_DIR/([^"]+)"', text) + re.findall(r"\$ECI_LIB_DIR/([a-z_]+\.py)", text):
            if not (CI_DIR / "lib" / rel).exists():
                missing.append(f"{script.name} → lib/{rel}")
    assert not missing, f"dangling CI helper references: {missing}"


@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="set RUN_SLOW=1 to exercise --help")
def test_every_ci_script_help_works():
    for script in sorted(CI_DIR.glob("*.sh")):
        proc = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True,
                              check=False, timeout=25)
        assert proc.returncode == 0, f"{script.name} --help failed: {proc.stderr[:400]}"
        assert f"scripts/ci/{script.name}" in proc.stdout, f"{script.name} --help printed nothing useful"
