# network-policies (Phase 4) ✅ — Default-Deny + Allow Edges

Per-namespace `NetworkPolicy` objects, one file per edge, default-deny in. Phase 4 complete.

## Topology (from Phase 2, enforced by tests)

Phase 2 fixes the topology these must match, because smoke test in pipeline (`Jenkinsfile` → `E2E smoke test`) already exercises exactly these hops:

```
ingress → gateway:8080
gateway → identity:8001 product:8002 inventory:8003 cart:8004 order:8005
          payment:8006 shipping:8007 notification:8008 review:8009
product → postgres:5432   identity → postgres   order → postgres + rabbitmq
cart    → redis:6379      notification → rabbitmq:5672
```

Ports come from `scripts/ci/lib/services.sh`; if Phase 4 needs a hop gateway doesn't make, service code changes first and pipeline picks it up — a policy that allows traffic nothing sends is audit noise.

`tests/test_service_contracts.py` (app tier) proves fan-out direction by reading gateway's `ROUTES` table, so edge list cannot silently drift.

## Chart

- `Chart.yaml`: 0.1.0, appVersion 1.0.0
- `values.yaml`: image contract placeholder + enabled true + defaultDeny enabled Ingress+Egress + allowDNS enabled + gateway enabled port 8080 from ingress-nginx + ipBlock 0.0.0.0/0 except 169.254.0.0/16 + edges map per service (identity 8001 from gateway, product 8002 from gateway+inventory+cart+order+review, etc.) + infra postgres 5432 clients 8 services, redis 6379 clients cart, rabbitmq 5672 clients notification+order + namespace ecom
- `templates/`: _helpers.tpl, default-deny.yaml (podSelector {} policyTypes Ingress+Egress), allow-dns.yaml (egress to kube-system k8s-app kube-dns 53 UDP+TCP), gateway-ingress.yaml (podSelector gateway ingress from ingress-nginx controller + gateway + 0.0.0.0/0 port 8080), service-edges.yaml (range edges, per svc NetworkPolicy from gateway or specific clients port), infra-edges.yaml (range infra, per infra allow from clients port), NOTES.txt

## Install & Verify

```bash
helm upgrade --install network-policies ./helm-charts/network-policies -n ecom --create-namespace
kubectl get networkpolicies -n ecom
kubectl describe networkpolicy -n ecom network-policies-default-deny
kubectl describe networkpolicy -n ecom network-policies-gateway-ingress

# Test connectivity (from gateway pod)
kubectl exec -n ecom deploy/gateway -- curl -s http://product:8002/health
kubectl exec -n ecom deploy/product -- curl -s http://postgres:5432 # should fail (not HTTP but TCP allowed)
```

## Security Model

- Default deny all ingress in ecom namespace — zero trust
- Allow DNS for all pods — kube-dns/coredns 53
- Gateway ingress: only ingress-nginx → gateway:8080
- Service edges: gateway → each service, plus specific inter-service edges (order → product, inventory, payment, cart, shipping, notification, etc.)
- Infra edges: services → postgres:5432, cart → redis:6379, notification/order → rabbitmq:5672

This matches exactly what `scripts/ci/smoke.sh` and `scripts/test.sh` exercise, so a policy that blocks a real hop fails CI.

See `../README.md` Phase 4 section for full Helm usage and `../../terraform/aws-graviton/README.md` for VPC tags that enable ALB discovery.
