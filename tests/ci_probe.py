#!/usr/bin/env python3
"""In-container-style probe for one service (used by tests/test_service_contracts.py).

Run as:  python tests/ci_probe.py <service>
Prints exactly one machine-readable line:  ECI_PROBE {...json...}

Why a subprocess: every service ships a package literally named `app`, so they
cannot be imported side by side. It also lets us neutralise the Phase 1
startup-retry sleeps (cart pings Redis 10x at import time) without touching
production code, and points every DATABASE_URL at a closed localhost port so the
probe proves "the app builds and answers /health with no infrastructure" — the
same contract scripts/ci/smoke.sh asserts against the real image.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def neutralise_retries() -> None:
    """Phase 1 services retry their dependency connection at import time.

    For a probe that must run in milliseconds we make sleep a no-op. The
    connection attempt itself still happens (and still fails fast on a closed
    port), so the resilience path is exercised, just not slept through.
    """
    time.sleep = lambda *_a, **_k: None  # type: ignore[assignment]


def configure_env(service: str) -> None:
    os.environ.setdefault("DATABASE_URL", "postgresql://eci:eci@127.0.0.1:1/eci_probe")
    os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
    os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:1/")
    os.environ.setdefault("SECRET_KEY", "eci-probe-secret-key-not-for-production")
    os.environ.setdefault("ALGORITHM", "HS256")
    os.environ.setdefault("ENFORCE_AUTH", "false")
    for key in ("IDENTITY", "PRODUCT", "INVENTORY", "CART", "ORDER", "PAYMENT",
                "SHIPPING", "NOTIFICATION", "REVIEW", "GATEWAY"):
        os.environ.setdefault(f"{key}_SERVICE_URL", "http://127.0.0.1:1")
    if service == "gateway":
        # An empty service map keeps /health from spending its 2s timeout per
        # downstream while probing; resolve_service() is still fully exercised.
        os.environ["SERVICES"] = "{}"


def collect(service: str) -> dict:
    neutralise_retries()
    configure_env(service)
    sys.path.insert(0, str(REPO / "services" / service))

    report: dict = {"service": service, "ok": False, "errors": []}
    try:
        module = __import__("app.main", fromlist=["app"])
    except Exception as exc:  # noqa: BLE001 — a failed import IS the finding
        report["errors"].append(f"import app.main failed: {type(exc).__name__}: {exc}")
        return report

    app = getattr(module, "app", None)
    if app is None:
        report["errors"].append("app.main defines no `app` object")
        return report

    report["title"] = getattr(app, "title", None)
    report["version"] = getattr(app, "version", None)
    report["routes"] = sorted({r.path for r in getattr(app, "routes", [])})
    report["route_count"] = len(getattr(app, "routes", []))
    # /openapi.json is what the gateway's route table and every Swagger UI link to
    report["has_openapi"] = "/openapi.json" in report["routes"] or hasattr(app, "openapi")

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"TestClient unavailable: {exc}")
        return report

    client = TestClient(app, base_url="http://eci.test")

    r = client.get("/health")
    report["health_status"] = r.status_code
    try:
        report["health_body"] = r.json()
    except Exception:  # noqa: BLE001
        report["health_body"] = {"raw": r.text[:200]}

    r = client.get("/health", headers={"X-Request-ID": "eci-probe-rid"})
    report["request_id_echo"] = r.headers.get("x-request-id")
    report["service_header"] = r.headers.get("x-service")

    m = client.get("/metrics")
    report["metrics_status"] = m.status_code
    body = m.text or ""
    wanted = ("http_requests_total", "http_request_duration_seconds", "python_gc_objects_collected_total")
    report["metrics_samples"] = {name: (name in body) for name in wanted}

    report["unknown_path_status"] = client.get("/eci-definitely-not-a-route").status_code

    extras = service_extras(service, module, client)
    if extras:
        report["extras"] = extras

    report["ok"] = not report["errors"]
    return report


def service_extras(service: str, module, client) -> dict:  # noqa: ANN001 - TestClient
    """Service-specific unit assertions that belong to a contract test, not a smoke run."""
    extras: dict = {}
    if service == "identity":
        auth = __import__("app.auth", fromlist=["verify_password"])
        h = auth.get_password_hash("Str0ng-Pass!")
        extras["bcrypt_roundtrip"] = bool(auth.verify_password("Str0ng-Pass!", h))
        extras["bcrypt_rejects_wrong"] = not auth.verify_password("wrong", h)
        extras["hash_is_salted"] = h != auth.get_password_hash("Str0ng-Pass!")
        tok = auth.create_access_token({"sub": "probe"})
        from jose import jwt as _jwt
        cfg = __import__("app.config", fromlist=["settings"]).settings
        decoded = _jwt.decode(tok, cfg.secret_key, algorithms=[cfg.algorithm])
        extras["jwt_sub"] = decoded.get("sub")
        extras["jwt_has_exp"] = "exp" in decoded
        try:
            _jwt.decode(tok, "the-wrong-secret", algorithms=[cfg.algorithm])
            extras["jwt_rejects_foreign_key"] = False
        except Exception:  # noqa: BLE001
            extras["jwt_rejects_foreign_key"] = True
    elif service == "review":
        schemas = __import__("app.schemas", fromlist=["ReviewCreate"])
        ok = schemas.ReviewCreate(product_id=1, user_id="u", rating=5)
        extras["rating_5_ok"] = ok.rating == 5
        for bad in (0, 6, -1):
            try:
                schemas.ReviewCreate(product_id=1, user_id="u", rating=bad)
                extras.setdefault("rating_bounds_enforced", False)
            except Exception:  # noqa: BLE001
                extras.setdefault("rating_bounds_enforced", True)
    elif service == "gateway":
        main = module
        resolve = getattr(main, "resolve_service", None)
        if resolve:
            got = resolve("/api/products/42")
            extras["resolve_products"] = list(got) if isinstance(got, tuple) else str(got)
            extras["resolve_unknown"] = str(resolve("/nope"))
        extras["routes_table"] = sorted(set(getattr(main, "ROUTES", {})))
    return extras


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: ci_probe.py <service>", file=sys.stderr)
        return 2
    service = argv[0]
    try:
        report = collect(service)
    except Exception as exc:  # noqa: BLE001 — the probe must always emit a line
        report = {"service": service, "ok": False, "errors": [f"probe crashed: {type(exc).__name__}: {exc}"]}
    print("ECI_PROBE " + json.dumps(report, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
