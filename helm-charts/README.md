# helm-charts/ — Phase 4 ✅ Production K8s Orchestration

**Phase 2** delivered the bump contract (image.repository + image.tag). **Phase 4** delivers the chart bodies that consume it — 10 microservices + ecom-common library + ingress-nginx + network-policies, all Graviton ARM64 ready.

## Layout (Phase 4 Complete)

```
helm-charts/
├── ecom-common/           # Library chart — labels, probes, security, scheduling, PDB, HPA
│   ├── Chart.yaml         # type: library, version 0.1.0
│   └── templates/_helpers.tpl, _probes.tpl
├── identity/              # 8001 — critical, on-demand Graviton, PDB, no spot toleration
│   ├── Chart.yaml         # dependencies: ecom-common file://../ecom-common
│   ├── values.yaml        # image contract + resources + autoscaling + scheduling
│   └── templates/{deployment,service,configmap,hpa,serviceaccount,pdb,networkpolicy,ingress,NOTES.txt,_helpers.tpl}
├── product/               # 8002 — stateless, spot, Graviton, 2 replicas, HPA 2-10
├── inventory/             # 8003 — stateless spot
├── cart/                  # 8004 — Redis, stateless spot
├── order/                 # 8005 — critical on-demand (orchestration)
├── payment/               # 8006 — critical on-demand (money path, no spot eviction)
├── shipping/              # 8007 — critical on-demand
├── notification/          # 8008 — RabbitMQ, stateless spot
├── review/                # 8009 — stateless spot
├── gateway/               # 8080 — edge, critical, Ingress /* → gateway:8080, strips /api
├── ingress-nginx/         # Ingress controller wrapper — multi-arch, LoadBalancer (EKS) / NodePort (Kind)
│   ├── Chart.yaml         # dependency: ingress-nginx 4.10.0 from kubernetes.github.io
│   ├── values.yaml        # controller replica 2, Graviton nodeSelector, Prometheus annotations
│   └── templates/NOTES.txt
└── network-policies/      # Security — default-deny + allow edges matching smoke test topology
    ├── Chart.yaml
    ├── values.yaml        # edges: gateway→services, services→postgres/redis/rabbitmq
    └── templates/{default-deny,allow-dns,gateway-ingress,service-edges,infra-edges}
```

## The Bump Contract (CI owns, still enforced)

`scripts/ci/gitops-bump.sh` rewrites exactly two keys per service:

```yaml
image:
  repository: docker.io/mylab12345/ecom-product   # from ECOM_IMAGE_PREFIX
  tag: "0.0.0+phase1-placeholder"                # rewritten to <branch>-<sha>
```

Rules (enforced by `tests/test_ci_hygiene.py`):

- `image:` top-level mapping, keys indented **exactly two spaces**
- `tag` stays quoted — YAML 1.1 reads unquoted `0123` as octal
- `service.port` / `containerPort` must equal port in `scripts/ci/lib/services.sh` and `EXPOSE` in Dockerfile

Nothing else is CI's business — Phase 4 adds `resources`, `autoscaling`, `scheduling`, `ingress`, `networkPolicy` around that contract.

## Each Service Chart (Phase 4)

**Deployment (2 replicas, probes, resources):**

- `replicaCount: 2` — HPA scales 2-10 on 70% CPU, 80% memory
- Probes: `path: /health` — same path Dockerfile HEALTHCHECK and Phase 2 smoke test use. One health contract, three consumers.
- Resources: requests 100m/128Mi, limits 500m/512Mi — Graviton sane defaults
- Security: non-root (1000), fsGroup 1000, seccomp RuntimeDefault, drop ALL, no priv escalation
- Scheduling: `kubernetes.io/arch: arm64` nodeSelector when `scheduling.architecture=arm64`, `spot=true:NoSchedule` toleration when `spotCapable=true`
- TopologySpreadConstraints: spread across `topology.kubernetes.io/zone` and `kubernetes.io/hostname`
- PriorityClass: `ecom-critical` for identity, order, payment, shipping, gateway; `ecom-best-effort` for stateless
- Env: ConfigMap from `values.config` (SERVICE_NAME, LOG_LEVEL, downstream URLs via K8s DNS like `http://product:8002`)

**Service (ClusterIP):**

- Type ClusterIP, port = targetPort = containerPort = registry port
- Annotations for Prometheus scrape

**ConfigMap:**

- Renders `values.config` as env vars, checksum annotation triggers rollout on config change

**HPA:**

- `autoscaling/v2`, min 2 max 10, CPU 70%, memory 80%

**ServiceAccount + PDB:**

- SA with automount true, PDB minAvailable 1

**NetworkPolicy (per-chart):**

- Ingress: only from gateway (and ingress-nginx namespace) + prometheus
- Egress: kube-dns 53/UDP+TCP + downstream dependencies (product→postgres, cart→redis+product, etc.)

**Ingress:**

- Gateway: enabled, className nginx, host ecom.local, path `/*` → gateway:8080, rewrite-target `/$2`
- Other services: disabled by default (gateway is only HTTP entrypoint, it strips `/api` and forwards)

## ecom-common Library

Shared helpers used by all 10 charts (and can be used directly via `include "ecom-common.labels"` if dependency is updated):

| Helper | Purpose |
|--------|---------|
| `fullname`, `chart`, `selectorLabels`, `labels` | `app.kubernetes.io/*` + `eci.managed-by=jenkins-ci` + `eci.phase=4` + arch |
| `serviceAccountName`, `image` | SA name, `repository:tag` |
| `probes.readiness`, `probes.liveness` | HTTP probe from `values.probes` |
| `resources`, `nodeSelector`, `tolerations`, `topologySpreadConstraints` | Graviton + spot scheduling |
| `securityContext`, `podSecurityContext`, `containerSecurityContext` | non-root, seccomp |
| `env`, `priorityClassName`, `pdb` | ConfigMap env, priorityClass, PDB |

## ingress-nginx (Multi-arch)

Wrapper around upstream `ingress-nginx` 4.10.0 chart:

- Image `registry.k8s.io/ingress-nginx/controller:v1.10.0` — multi-arch (amd64+arm64), do NOT pin amd64-only digest
- Replica 2, HPA 2-6, PDB minAvailable 1, topology spread across AZs
- Service: LoadBalancer on EKS (NLB annotations), NodePort + hostPort on Kind
- Config: `proxy-body-size 10m`, `proxy-read-timeout 60`, `use-forwarded-headers true`, `log-format-upstream` includes `$req_id` for tracing
- NodeSelector `kubernetes.io/arch: arm64` — Graviton by default
- Gateway is only entrypoint: `/api/products/health` → gateway:8080 → product:8002. Ingress must NOT rewrite paths except gateway's `/*` → `/$2`, or two layers fight.

**Install:**

```bash
# EKS (from terraform/aws-graviton)
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace

# Kind (local)
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace \
  --set ingress-nginx-upstream.controller.service.type=NodePort \
  --set ingress-nginx-upstream.controller.hostPort.enabled=true
```

## network-policies (Security)

Per-namespace NetworkPolicy, one file per edge, default-deny in.

**Topology enforced (matches smoke test + gateway ROUTES):**

```
ingress → gateway:8080
gateway → identity:8001 product:8002 inventory:8003 cart:8004 order:8005 payment:8006 shipping:8007 notification:8008 review:8009
product → postgres:5432   identity → postgres   order → postgres + rabbitmq
cart    → redis:6379      notification → rabbitmq:5672
```

- `default-deny`: blocks all ingress unless explicitly allowed
- `allow-dns`: allows kube-dns/coredns 53/UDP+TCP for all pods
- `gateway-ingress`: allows ingress-nginx → gateway:8080 + 0.0.0.0/0 (except link-local)
- `service-edges`: per-service allow from gateway + specific downstream clients
- `infra-edges`: postgres:5432 clients, redis:6379 clients, rabbitmq:5672 clients

Ports from `scripts/ci/lib/services.sh`; if Phase 4 needs a hop gateway doesn't make, service code changes first and pipeline picks it up — a policy that allows traffic nothing sends is audit noise.

`tests/test_service_contracts.py` (app tier) proves fan-out direction by reading gateway's ROUTES table, so edge list cannot silently drift.

**Install:**

```bash
helm upgrade --install network-policies ./helm-charts/network-policies -n ecom --create-namespace
kubectl get networkpolicies -n ecom
```

## Helm Usage (Phase 4)

```bash
# Lint all charts (requires helm)
for chart in identity product inventory cart order payment shipping notification review gateway; do
  helm lint helm-charts/$chart
  helm template ecom helm-charts/$chart -n ecom | kubectl apply --dry-run=client -f -
done
helm lint helm-charts/ecom-common
helm lint helm-charts/ingress-nginx
helm lint helm-charts/network-policies

# Dependency update (library chart)
helm dependency update helm-charts/product
helm dependency update helm-charts/gateway
# Or for all:
for d in helm-charts/*/; do helm dependency update $d 2>/dev/null || true; done

# Deploy to Kind (local) — from terraform/local-kind
export KUBECONFIG=$(terraform -chdir=terraform/local-kind output -raw kubeconfig_path)
helm upgrade --install ecom-common ./helm-charts/ecom-common -n ecom --create-namespace # library, no resources
for svc in identity product inventory cart order payment shipping notification review gateway; do
  helm upgrade --install $svc ./helm-charts/$svc -n ecom \
    --set image.repository=localhost:5001/ecom-$svc \
    --set image.tag=local
done
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace \
  --set ingress-nginx-upstream.controller.service.type=NodePort \
  --set ingress-nginx-upstream.controller.hostPort.enabled=true
helm upgrade --install network-policies ./helm-charts/network-policies -n ecom

# Deploy to EKS (from terraform/aws-graviton)
aws eks update-kubeconfig --region us-east-1 --name ecom-eks-graviton
helm upgrade --install network-policies ./helm-charts/network-policies -n ecom --create-namespace
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace
for svc in identity product inventory cart order payment shipping notification review gateway; do
  helm upgrade --install $svc ./helm-charts/$svc -n ecom --set image.tag=$(git rev-parse --short HEAD)
done

# Verify
kubectl get pods -n ecom -l eci.phase=4 -L eci.service -L kubernetes.io/arch
kubectl get hpa -n ecom
kubectl get pdb -n ecom
kubectl get networkpolicies -n ecom
kubectl get ingress -n ecom
```

## Cost & Scheduling (from Phase 3 terraform)

Outputs from `terraform/aws-graviton` provide scheduling overrides:

```yaml
scheduling:
  architecture: arm64
  spotCapable: true # stateless only
  priorityClass: ecom-best-effort
```

Charts already read these to set `nodeSelector.kubernetes.io/arch=arm64` and tolerations `spot=true:NoSchedule` for stateless spot pools. Critical pods (identity, order, payment, shipping, gateway) have `spotCapable=false` and `priorityClass: ecom-critical`, so they land on ON_DEMAND Graviton nodes.

## Preview without touching git

```bash
make ci-plan      # what repository/tag CI would write
make gitops-dry   # the bump, applied to a copy under .ci-output — tree untouched
helm template ecom helm-charts/product -n ecom --set image.tag=local | head -n 100
```

`gitops-dry` writes `.ci-output/gitops-bump.patch` in `git apply` format, so exact commit can be replayed locally and fed through `helm template` before anyone believes it.

## When Phase 5 replaces this

ArgoCD Application CRs will point at `helm-charts/<service>` path with `targetRevision: gitops/main`. Jenkins bumps tag → ArgoCD syncs within 3 min. CI never runs `kubectl apply` or `helm upgrade` — rollback is `git revert`, not "replay build 7".
