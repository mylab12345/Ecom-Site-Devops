"""App tier (marker `app`): the ten services as HTTP contracts.

Each service is imported and driven with FastAPI's TestClient in an isolated
subprocess (tests/ci_probe.py — all ten ship a package literally named `app`, so
they cannot share a process). No Postgres, Redis or RabbitMQ is started: the point
is that a container with no dependencies still *serves*, because that is what the
Phase 4 probes and scripts/ci/smoke.sh will assert against a real pod.

Run:  make ci-test        (auto: skipped if the service deps are not installed)
      make ci-test-docker (what Jenkins runs)
"""

from __future__ import annotations

import pytest

from conftest import SERVICE_NAMES, port_of, registry_for

pytestmark = pytest.mark.app

# Applied to the generic tests only: the service-specific tests below assert
# behaviour that exists in exactly one service, and parametrising those over ten
# services would produce nine meaningless passes.
per_service = pytest.mark.parametrize("service", SERVICE_NAMES, ids=SERVICE_NAMES)

# What each service owes the rest of the platform. Keep this table boring: it is
# the contract Phase 4's probes, Phase 6's dashboards and the gateway routes rely on.
REQUIRED_ROUTES = {
    "identity": {"/auth/register", "/auth/login", "/auth/verify", "/users/me", "/health", "/metrics"},
    "product": {"/products", "/products/{product_id}", "/categories/list", "/health", "/metrics"},
    "inventory": {"/health", "/metrics"},
    "cart": {"/cart/{user_id}", "/cart/{user_id}/items", "/cart/{user_id}/count", "/health", "/metrics"},
    "order": {"/orders", "/orders/{order_id}", "/health", "/metrics"},
    "payment": {"/payments", "/payments/{payment_id}/process", "/health", "/metrics"},
    "shipping": {"/shipments", "/shipments/track/{tracking_number}", "/health", "/metrics"},
    "notification": {"/notifications/send", "/notifications/user/{user_id}", "/health", "/metrics"},
    "review": {"/reviews", "/reviews/product/{product_id}", "/reviews/product/{product_id}/stats", "/health", "/metrics"},
    "gateway": {"/", "/health", "/metrics", "/{path:path}"},
}


@per_service
def test_app_imports_and_reports_no_errors(service_probes, service):
    rep = service_probes[service]
    assert rep.get("errors") == [], f"{service}: {rep.get('errors')}"
    assert rep.get("ok") is True


@per_service
def test_health_endpoint_answers_without_any_dependency(service_probes, service):
    """200 with a status of healthy *or* degraded.

    "degraded" is a pass here on purpose: a service that reports its dependency as
    down is behaving correctly, while one that 500s (or never binds) breaks the
    gateway's aggregated health and every readiness probe in the cluster.
    """
    rep = service_probes[service]
    assert rep["health_status"] == 200, f"{service}: /health → {rep['health_status']}"
    assert rep["health_body"].get("status") in {"healthy", "degraded"}, rep["health_body"]
    assert "service" in rep["health_body"] or service == "gateway", rep["health_body"]


@per_service
def test_declares_every_required_route(service_probes, service):
    rep = service_probes[service]
    missing = REQUIRED_ROUTES[service] - set(rep["routes"])
    assert not missing, f"{service} does not expose {sorted(missing)} (routes: {rep['routes']})"


@per_service
def test_prometheus_metrics_endpoint(service_probes, service):
    rep = service_probes[service]
    assert rep["metrics_status"] == 200, f"{service}: /metrics → {rep['metrics_status']}"
    missing = [name for name, present in rep["metrics_samples"].items() if not present]
    assert not missing, f"{service}: /metrics is missing {missing} — Phase 6 dashboards key off these names"


@per_service
def test_request_id_is_propagated(service_probes, service):
    """Phase 1's X-Request-ID convention, asserted rather than assumed: a dropped
    header here is what makes 'which service was slow' unanswerable later."""
    rep = service_probes[service]
    assert rep["request_id_echo"] == "eci-probe-rid", (
        f"{service}: middleware did not echo X-Request-ID (got {rep['request_id_echo']!r})"
    )
    if service != "gateway":
        assert rep["service_header"], f"{service}: middleware must set X-Service"


@per_service
def test_unknown_path_is_404_not_500(service_probes, service):
    rep = service_probes[service]
    assert rep["unknown_path_status"] in (404, 405), f"{service}: unknown path → {rep['unknown_path_status']}"


@per_service
def test_service_name_matches_registry(service_probes, service):
    """Every service announces itself, and the name must match the CI registry:
    Grafana legends, log lines and Phase 4 NetworkPolicy names key off it.

    The gateway is the deliberate exception — as the edge it stamps
    `X-Forwarded-By` and reports a `gateway` field instead of `service`. That
    asymmetry is Phase 1 behaviour; asserting it here documents it instead of
    letting a future refactor "fix" it silently.
    """
    rep = service_probes[service]
    if service == "gateway":
        assert rep["health_body"].get("gateway") == "api-gateway", rep["health_body"]
    else:
        declared = rep["health_body"].get("service") or rep["service_header"]
        assert declared == f"{service}-service", f"{service}: reports itself as {declared!r}"
    assert port_of(service) == int(registry_for(service)["port"])


@per_service
def test_openapi_is_served(service_probes, service):
    """FastAPI's /docs is the Phase 1 developer UX and the source of the gateway
    route table shown at http://:8080/ — losing it is a visible regression."""
    assert service_probes[service]["has_openapi"] is True


# ── service-specific behaviour (still no I/O beyond the app itself) ──────────
def test_identity_password_and_token_primitives(service_probes):
    extras = service_probes["identity"].get("extras") or {}
    assert extras, "identity probe did not run its auth checks"
    assert extras["bcrypt_roundtrip"] is True
    assert extras["bcrypt_rejects_wrong"] is True
    assert extras["hash_is_salted"] is True, "identical hashes would leak password equality"
    assert extras["jwt_sub"] == "probe"
    assert extras["jwt_has_exp"] is True, "an unbounded token cannot be revoked"
    assert extras["jwt_rejects_foreign_key"] is True, "HS256 must not accept another signing key"


def test_review_rating_bounds(service_probes):
    extras = service_probes["review"].get("extras") or {}
    assert extras.get("rating_5_ok") is True
    assert extras.get("rating_bounds_enforced") is True, "rating must reject 0/6/-1 (Field ge=1 le=5)"


def test_gateway_route_table_shape(service_probes):
    extras = service_probes["gateway"].get("extras") or {}
    table = set(extras.get("routes_table") or [])
    for prefix in ("/api/products", "/api/orders", "/api/payments", "/api/cart", "/api/auth"):
        assert prefix in table, f"gateway lost {prefix}; /metrics consumers and README §4.2 both assume it"
    resolve = extras.get("resolve_products")
    assert resolve == ["product", "/products/42"], f"gateway path rewriting broke: {resolve}"
    assert str(extras.get("resolve_unknown", "")).startswith("(None"), "unrouted paths must not be proxied"
