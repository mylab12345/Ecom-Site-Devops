"""Shared fixtures for the Phase 2 CI test suite.

Two tiers:
  structure (default) — stdlib only, so `pytest -m structure` runs on a bare
                        agent in <2s and guards the CI ↔ repo contracts
  app                 — imports the FastAPI services, needs their requirements

Everything here must keep working without PyYAML/httpx/pytest plugins: the
structure tier is what the Jenkins lint stage runs before anything is installed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = REPO_ROOT / "services"
CHARTS_DIR = REPO_ROOT / "helm-charts"
CI_DIR = REPO_ROOT / "scripts" / "ci"
CHECK_JENKINSFILE = CI_DIR / "lib" / "check_jenkinsfile.py"
JENKINSFILE = REPO_ROOT / "Jenkinsfile"
REGISTRY_FILE = CI_DIR / "lib" / "services.sh"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yaml"
GATEWAY_CONFIG = SERVICES_DIR / "gateway" / "app" / "config.py"
INFRA_SERVICES = {"postgres", "redis", "rabbitmq"}

# The registry lives in bash (scripts/ci/lib/services.sh) because that is what
# Jenkins and the local scripts actually consume. These tests parse *that* file
# rather than restating the table, so the suite cannot drift from the pipeline.
_SERVICE_ROW = re.compile(r'^\s*"([a-z]+)\|(\d{4})\|(/[A-Za-z0-9/_-]*)\|([A-Za-z0-9-]+)\|([a-z]+)"')


def parse_service_registry() -> list[dict[str, str]]:
    text = REGISTRY_FILE.read_text(encoding="utf-8")
    block = text.split("ECOM_CI_SERVICES_RAW=(", 1)
    assert len(block) == 2, "scripts/ci/lib/services.sh: ECOM_CI_SERVICES_RAW table not found"
    rows: list[dict[str, str]] = []
    for line in block[1].split(")", 1)[0].splitlines():
        m = _SERVICE_ROW.match(line)
        if not m:
            continue
        rows.append(
            {
                "name": m.group(1),
                "port": int(m.group(2)),  # type: ignore[typeddict-item]
                "prefix": m.group(3),
                "depends": m.group(4),
                "tier": m.group(5),
            }
        )
    return rows


REGISTRY = parse_service_registry()
SERVICE_NAMES = [s["name"] for s in REGISTRY]


def registry_for(name: str) -> dict[str, str]:
    return next(entry for entry in REGISTRY if entry["name"] == name)


def port_of(name: str) -> int:
    return int(registry_for(name)["port"])


# --- tiny line-oriented parsers (no PyYAML in the structure tier) -----------
def parse_compose_services(text: str) -> dict[str, list[str]]:
    """Map compose service → list of its direct child keys (2-space indent)."""
    services: dict[str, list[str]] = {}
    in_services = False
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            in_services = raw.rstrip() == "services:"
            current = None
            continue
        if not in_services:
            continue
        if indent == 2:
            m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", raw)
            current = m.group(1) if m else None
            if current:
                services[current] = []
            continue
        if indent == 4 and current:
            m = re.match(r"^    ([A-Za-z0-9_]+):", raw)
            if m:
                services[current].append(m.group(1))
    return services


def parse_compose_ports(text: str) -> dict[str, list[str]]:
    """service → ["8001:8001", ...] from the ports list."""
    ports: dict[str, list[str]] = {}
    current = None
    in_ports = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 2:
            m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", raw)
            current = m.group(1) if m else None
            in_ports = False
            if current:
                ports.setdefault(current, [])
            continue
        if current is None:
            continue
        if indent == 4 and raw.strip().startswith("ports:"):
            in_ports = True
            continue
        if indent == 4:
            in_ports = False
        if in_ports and indent >= 6 and raw.strip().startswith("-"):
            val = raw.strip()[1:].strip().strip('"').strip("'")
            if val:
                ports[current].append(val)
    return ports


# --- service-app probe (app tier) -------------------------------------------
PROBE = Path(__file__).resolve().parent / "ci_probe.py"


@pytest.fixture(scope="session")
def service_probes():
    """Boot each service's FastAPI app in an isolated subprocess and cache the report.

    Isolation matters: all 10 services expose a package literally named `app`,
    so importing them into one process would shadow each other. A subprocess also
    reproduces what the container does (`uvicorn app.main:app`) and lets us stub
    the connection-retry sleeps without touching product code.
    """
    if os.environ.get("ECOM_SKIP_APP_TIER") == "1":
        pytest.skip("app tier skipped via ECOM_SKIP_APP_TIER=1")
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
    except ImportError:  # pragma: no cover - environment dependent
        pytest.skip("service dependencies not installed: pip install -r requirements-dev.txt "
                    "plus each services/*/requirements.txt (or: make ci-test-docker)")

    reports: dict[str, dict] = {}
    for name in SERVICE_NAMES:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, str(PROBE), name],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("ECOM_PROBE_TIMEOUT", "120")),
            env={
                **os.environ,
                "PYTHONPATH": f"{SERVICES_DIR / name}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
                "PYTHONDONTWRITEBYTECODE": "1",
                "ECI_QUIET": "1",
            },
            check=False,
        )
        payload = _last_json(proc.stdout)
        if payload is None:
            pytest.fail(f"{name}: probe produced no JSON (exit {proc.returncode})\n"
                        f"stdout tail: {proc.stdout[-2000:]}\nstderr tail: {proc.stderr[-2000:]}")
        reports[name] = payload
    return reports


def _last_json(text: str) -> dict | None:
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        if line.startswith("ECI_PROBE "):
            try:
                return json.loads(line[len("ECI_PROBE "):])
            except json.JSONDecodeError:
                continue
    return None
