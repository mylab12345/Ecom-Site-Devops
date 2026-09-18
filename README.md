# E-Commerce Microservices Platform

Ten FastAPI microservices plus the full delivery platform around them: Docker Compose for
local dev, a Jenkins CI pipeline that builds multi-arch images and gates them on Trivy,
Terraform for Kind (laptop) and EKS on Graviton (AWS), Helm charts, ArgoCD GitOps, and a
Prometheus/Grafana/Loki/Jaeger observability stack.

Everything runs on your laptop first. `make ci` executes the same checks Jenkins does, so a
red pipeline is reproducible in one command.

**Status — all six phases delivered:** Phase 1 ✅ local stack · Phase 2 ✅ CI pipeline ·
Phase 3 ✅ Terraform · Phase 4 ✅ Helm · Phase 5 ✅ ArgoCD GitOps · Phase 6 ✅ observability.

```
  compose (local) ──► Jenkins CI ──► git commit ──► ArgoCD ──► Kind / EKS Graviton
                     build + scan      image tags     reconciles    Helm charts
                                                                        │
                                             Prometheus · Grafana · Loki · Jaeger · Trivy
```

---

## 1. Architecture

```
                          ┌──────────────┐
     client ─── :8080 ───►│ API Gateway  │──► optional JWT verification
                          └──────┬───────┘
        ┌──────────┬─────────────┼─────────────┬──────────┬──────────┐
   identity     product      inventory        cart      order     review
     :8001        :8002         :8003         :8004     :8005      :8009
                                                       ┌────┴────┐
                                                    payment   shipping
                                                     :8006     :8007
                                                       └────┬────┘
                                                       notification :8008

   Postgres 15 (one instance, nine logical DBs) · Redis 7 · RabbitMQ 3
```

| # | Service | Port | Gateway prefix | Store | What it actually does |
|---|---------|------|----------------|-------|-----------------------|
| 1 | identity | 8001 | `/api/auth` | Postgres | Register/login, JWT (HS256), bcrypt, RBAC, token verify |
| 2 | product | 8002 | `/api/products` | Postgres | Catalogue CRUD, full-text search, filters, pagination, seeds 8 products |
| 3 | inventory | 8003 | `/api/inventory` | Postgres | Stock vs. reserved, reserve/release/commit, movement log |
| 4 | cart | 8004 | `/api/cart` | Redis | 7-day TTL carts, enriches items by calling product |
| 5 | order | 8005 | `/api/orders` | Postgres | Orchestration: validate → reserve stock → clear cart → trigger shipping/notification |
| 6 | payment | 8006 | `/api/payments` | Postgres | Idempotent transactions, refunds, webhook, 5% simulated failure |
| 7 | shipping | 8007 | `/api/shipments` | Postgres | Tracking numbers, status machine pending → shipped → delivered |
| 8 | notification | 8008 | `/api/notifications` | Postgres + RabbitMQ | Email/SMS/push mocks, bulk send |
| 9 | review | 8009 | `/api/reviews` | Postgres | 1–5 ratings, averages and distribution, one review per user per product |
| 10 | gateway | 8080 | `/` | — | Prefix routing, 3× retry with backoff, `X-Request-ID` propagation |

Services talk over internal DNS — `http://product:8002/products/1` in Compose,
`http://product.ecom.svc.cluster.local:8002` in Kubernetes. The same name works in both, which
is why nothing needs rewriting between environments. Every service exposes `/health` (the same
path used by the Dockerfile HEALTHCHECK, the K8s probes, and the CI smoke stage), `/metrics`,
and echoes `X-Request-ID` + `X-Service`.

`scripts/ci/lib/services.sh` is the single source of truth for this table. `tests/` asserts it
against `docker-compose.yaml`, the gateway route map, and every Dockerfile, so the registry
cannot silently drift.

---

## 2. Quickstart (local)

**Needs:** Docker Engine 24+ with Compose v2.20+, Make, curl, jq, Python 3.11+, ~8 GB RAM and
20 GB disk. Free ports: 5432, 6379, 5672, 15672, 8001–8009, 8080.

```bash
git clone https://github.com/mylab12345/Ecom-Site-Devops.git && cd Ecom-Site-Devops

cp .env.example .env       # change SECRET_KEY before anything resembling production
make up                    # builds and starts 13 containers
make health                # gateway's aggregated view of all ten services
make test                  # integration smoke test across the whole flow
```

First boot takes ~60 s: Postgres initialises nine databases, product seeds its catalogue, and
Compose waits on healthchecks before starting dependents. Open
[`http://localhost:8080/docs`](http://localhost:8080/docs) for the gateway's Swagger UI.

```bash
make ps          # container state        make logs     # tail everything
make seed        # demo user + cart + order
make down        # stop                   make clean    # stop and wipe volumes
make doctor      # is the CI toolchain present? (docker, buildx, trivy, hadolint)
```

Port 8080 is the only public entry point in a real deployment; hitting 8001–8009 directly is a
local-dev convenience.

---

## 3. End-to-end demo

The whole purchase path, through the gateway. `./scripts/test.sh` runs this with assertions.

```bash
GW=http://localhost:8080

# register, then log in and keep the token
curl -sX POST $GW/api/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","username":"alice","password":"StrongPass123","full_name":"Alice"}'
TOKEN=$(curl -sX POST $GW/api/auth/login -d 'username=alice&password=StrongPass123' | jq -r .access_token)

# browse and search the catalogue
curl -s "$GW/api/products?q=headphones&category=Electronics" | jq

# check stock (bump it with POST /api/inventory/1/adjust -d '{"delta":50}')
curl -s $GW/api/inventory/1 | jq

# add to cart (Redis-backed), then read it back enriched with product data
curl -sX POST $GW/api/cart/alice/items -H 'Content-Type: application/json' \
  -d '{"product_id":1,"quantity":2}'
curl -s $GW/api/cart/alice | jq

# place the order: reserves inventory and clears the cart
curl -sX POST $GW/api/orders -H 'Content-Type: application/json' -d '{
  "user_id":"alice","items":[{"product_id":1,"quantity":2},{"product_id":3,"quantity":1}],
  "shipping_address":{"street":"221B Baker","city":"London","zip":"NW1"}}' | jq

# pay — the Idempotency-Key makes retries safe; force_status:"failed" simulates a decline
curl -sX POST $GW/api/payments -H 'Content-Type: application/json' -H 'Idempotency-Key: alice-order-1' \
  -d '{"order_id":1,"user_id":"alice","amount":424.48}' | jq
curl -sX POST $GW/api/payments/1/process -H 'Content-Type: application/json' -d '{}' | jq

# ship and track
curl -sX PUT $GW/api/shipments/1/status -H 'Content-Type: application/json' \
  -d '{"status":"shipped","location":"LHR"}' | jq

# review, then look at the notification log
curl -sX POST $GW/api/reviews -H 'Content-Type: application/json' \
  -d '{"product_id":1,"user_id":"alice","rating":5,"title":"Amazing","comment":"Best headphones ever"}'
curl -s $GW/api/notifications/user/alice | jq
```

---

## 4. Repository layout

```
docker-compose.yaml        10 services + postgres + redis + rabbitmq, all with healthchecks
Makefile                   every workflow below; `make help` lists them
Jenkinsfile.local          the local pipeline: build → deploy to Kind → verify
Jenkinsfile.aws            the cloud pipeline: build → push → gate → promote → GitOps bump
.env.example               app config + CI knobs (registry, platforms, trivy, gitops)
pyproject.toml             ruff + pytest config: one definition of "clean"
services/<name>/           Dockerfile · requirements.txt · app/{main,models,schemas,config}.py
scripts/init-db.sql        creates the nine logical databases
scripts/seed.sh test.sh    demo data · integration smoke test (needs the stack up)
scripts/ci/                the pipeline's real logic: lint, unit-tests, build, smoke,
                           scan, gitops-bump, all + lib/ (Jenkins calls these)
tests/                     structure tier (registry ↔ compose ↔ gateway ↔ Dockerfiles ↔
                           Jenkinsfile) and app tier (10 apps via TestClient, no infra)
terraform/local-kind/      Kind: 1 control plane + 2 workers + local registry on :5001
terraform/aws-graviton/    EKS 1.29 ARM64: VPC, IRSA, on-demand critical + spot stateless
helm-charts/               ecom-common library, 10 service charts, ingress-nginx,
                           network-policies (HPA, PDB, probes, security contexts)
argocd/                    AppProject, ApplicationSet, root App-of-Apps, 12 Applications
observability/             prometheus · grafana · loki · jaeger · security
```

Each subsystem has its own README: [`helm-charts/`](helm-charts/README.md),
[`terraform/local-kind/`](terraform/local-kind/README.md),
[`terraform/aws-graviton/`](terraform/aws-graviton/README.md),
[`argocd/`](argocd/README.md), [`observability/`](observability/README.md).

---

## 5. Continuous integration

Two pipelines, both deliberately boring: **declarative Jenkins only** — no `script { }`
blocks, no fan-out, no Groovy logic. Every stage is **one** `sh` call into `scripts/ci/` or
the Makefile, so the same commands run on your laptop and nothing needs debugging at 3 a.m.
`scripts/ci/lib/check_jenkinsfile.py` fails the lint if a `script` block ever comes back.

| Pipeline | Jenkins script path | Agent | What it does |
|----------|--------------------|-------|--------------|
| [`Jenkinsfile.local`](Jenkinsfile.local) | `Jenkinsfile.local` | `ecom-local` (docker, helm, kubectl, kind) | Build → deploy to Kind → verify. Nothing leaves the machine. |
| [`Jenkinsfile.aws`](Jenkinsfile.aws) | `Jenkinsfile.aws` | `ecom-buildx` (docker, buildx + QEMU, trivy) | Multi-arch build → push → security gate → promote → GitOps commit. ArgoCD deploys. |

**`Jenkinsfile.local` — build, deploy, verify**

| Stage | Runs | Fails when |
|-------|------|------------|
| Prepare | `make doctor` | a required tool is missing |
| Lint | `scripts/ci/lint.sh` | ruff findings, `bash -n`, YAML parse, secrets in git |
| Unit tests | `scripts/ci/unit-tests.sh` | any pytest failure |
| Build images | `scripts/ci/build.sh --push --registry localhost:5001` | build failure, missing manifest entry |
| Smoke test | `smoke.sh` | an image that will not boot or answer `/health` |
| Trivy scan | `scan.sh` (`TRIVY_INSECURE=1`: localhost:5001 is plain HTTP) | HIGH/CRITICAL with a released fix (`SCAN_GATE=true`; report-only by default locally) |
| Deploy to Kind | `make helm-install-local IMAGE_TAG=…` (kubeconfig from `make -s kind-kubeconfig`) | a Helm release will not install |
| Verify deployment | `kubectl wait --for=condition=Available` + `scripts/test.sh` | the rollout stalls, or the end-to-end flow breaks |

**`Jenkinsfile.aws` — publish, then hand over to ArgoCD**

| Stage | Runs | Fails when |
|-------|------|------------|
| Prepare | `make doctor`, buildx + QEMU, `docker login` | toolchain or registry credentials missing |
| Lint · Unit tests | as above, in `python:3.12-slim` with every service's deps | as above |
| Build & push | `build.sh --load` → `smoke.sh` → `build.sh --push` (amd64 + arm64, `retry(2)`) | build failure, `/health` not 200, push rejected |
| Trivy security gate | `scan.sh --mode gate` against the pushed refs | HIGH/CRITICAL CVE **with a released fix** (`.trivyignore` = accepted risk) |
| Promote images | `build.sh --promote --to latest` | registry-side retag failure |
| GitOps bump | `gitops-bump.sh --push` → `gitops/main`, trunk builds only | malformed `image:` block, push rejected, branch == base branch |

Run any of it yourself — these are the same scripts, in the same order:

```bash
make ci              # lint + unit tests, no Docker required (fast pre-push gate)
make ci-plan         # resolved build matrix: services × platforms × tags
make ci-all          # every stage in order, on this machine (Jenkins parity)
make ci-push REGISTRY=docker.io/mylab12345   # what Jenkinsfile.aws does
make helm-install-local && ./scripts/test.sh # what Jenkinsfile.local does after that
make gitops-dry      # preview the tag bump as a git-apply-able patch
```

### Five decisions worth knowing before you edit anything

1. **Candidate tag, then promote.** The candidate tag is the 12-char git SHA, derived by
   `build.sh` itself (with `-dirty` appended if the tree is not clean) — `scan.sh` and
   `--promote` derive the same value, so it cannot drift between stages, and rebuilding a
   commit is a no-op bump. `latest` is only ever written by the promote stage via
   `docker buildx imagetools create`, so a `latest` that skipped the Trivy gate cannot exist
   and the promoted bytes are exactly the scanned bytes.
2. **The cloud pipeline never deploys.** `Jenkinsfile.aws` ends with a commit touching two YAML
   lines per service (`image.repository`, `image.tag`) on `gitops/main`; ArgoCD reconciles, so a
   rollback is `git revert`, not "replay build #7". A test fails the build if `kubectl`,
   `helm upgrade` or `terraform apply` ever appears in that file. `Jenkinsfile.local` *does*
   deploy, because a Kind cluster on your own machine is the point of it.
3. **Multi-arch is the AWS default, `--load` is not.** buildx can't load a manifest list, so the
   smoke test runs against a single-platform build while the pushed manifest list is verified
   with `imagetools inspect`. QEMU/arm64 emulation is the slow part — `SKIP_MULTIARCH=1` buys a
   fast loop on feature branches. The local pipeline builds amd64 only: Kind runs on your host.
4. **Probes and healthchecks share one path.** `/health` in the Dockerfile, the readiness probe
   in Helm, and the CI smoke stage are the same endpoint, so a container that boots locally
   boots in the cluster.
5. **Missing tools fail loudly.** No trivy binary exits 3 rather than silently skipping the
   scan (`STRICT_TOOLS=1`). Nothing in either pipeline can fake a pass.

Agent setup, plugins, JCasC, credentials, webhooks, timings, and the failure table:
[`jenkins/setup.md`](jenkins/setup.md).

---

## 6. Deploying

```bash
# local Kind cluster + registry mirror on :5001 (free, mirrors the AWS topology)
make kind-up
export KUBECONFIG=$(terraform -chdir=terraform/local-kind output -raw kubeconfig_path)

# AWS EKS on Graviton (needs AWS credentials)
make tf-plan-eks && make tf-apply-eks && make eks-kubeconfig

# validate charts without a cluster, then install
make helm-lint && make helm-template
make helm-install-local && kubectl get pods -n ecom -l eci.phase=4 -L kubernetes.io/arch

# GitOps: install ArgoCD, bootstrap App-of-Apps, watch it reconcile
make argocd-install && make argocd-apps && make argocd-status
make argocd-admin-pass     # initial admin password
kubectl port-forward svc/argocd-server -n argocd 8081:80   # 8081: the gateway owns 8080 locally
```

Each chart ships a Deployment (2 replicas, rolling update, probes, resource limits, non-root
security context), Service, ConfigMap, HPA (2–10), PDB, NetworkPolicy, and an ingress route at
`/api/<service>`; the gateway owns `/*`. NetworkPolicies default-deny and open only the edges
in the service topology — cart can reach product and Redis, order can reach everything it
orchestrates, nothing else can.

Runbook for credentials, webhooks (<2 s sync), and audit-clean rollback:
[`argocd/setup.md`](argocd/setup.md).

### Cost posture on AWS

| Decision | Effect |
|----------|--------|
| Graviton `m7g`/`m6g` instead of `m7i`/`m6i` | ~20% cheaper, ~15% better perf/watt |
| Spot for stateless (product, inventory, cart, review, notification) | ~70% discount, eviction-tolerant |
| On-demand for critical (identity, order, payment, shipping, gateway) | no eviction mid-capture |
| Mixed instance types per node group | capacity availability |
| **Net** | **~40% below x86 on-demand** (`enable_spot`, `graviton_only` toggles) |

All images are `python:3.12-slim` — wheel-clean on both amd64 and arm64, so no cross-toolchain.

---

## 7. Observability and security

```bash
make obs-up        # Jaeger + kube-prometheus-stack + Grafana + Loki/Promtail + security
make obs-down      # tear it all down
make trivy-scan-cluster   # run the nightly scan now
```

- **Metrics/alerts** — `ServiceMonitor`s discover all ten services; `PrometheusRule`s fire on
  5xx > 1%, p99 > 1 s, instance down, payment failure spike, crash-looping pods.
- **Dashboards** — provisioned Grafana: platform overview (RPS, error rate, p50/p90/p99),
  per-service deep dive with a `$service` selector, and business SLOs (payment success 99.9%,
  availability 99.95%, order velocity). Login `admin` / `ecom-grafana-secure-admin`.
- **Logs** — Loki (TSDB v13, 7-day retention) with a Promtail DaemonSet indexing `service`,
  `level`, and `X-Request-ID`.
- **Traces** — Jaeger all-in-one (UI 16686, OTLP 4317/4318) with W3C `traceparent` and
  `X-Request-ID` propagated from the gateway.
- **Security** — nightly in-cluster Trivy CronJob at 02:00 UTC over running workloads, scoped
  read-only RBAC, and Pod Security Standards (`baseline` enforced, `restricted` audited).

```bash
kubectl port-forward -n observability svc/kube-prometheus-stack-grafana 3000:80
kubectl port-forward -n observability svc/kube-prometheus-stack-prometheus 9090:9090
kubectl port-forward -n observability svc/jaeger-all-in-one 16686:16686
```

Details: [`observability/README.md`](observability/README.md).

---

## 8. Pinned versions

| Layer | Versions |
|-------|----------|
| App | Python 3.12 (`python:3.12-slim`), FastAPI 0.110.2, Uvicorn 0.29.0, SQLAlchemy 2.0.30, Pydantic 2.7.1, httpx 0.27.0, psycopg2-binary 2.9.9 |
| Data | Postgres 15-alpine, Redis 7-alpine, RabbitMQ 3-management-alpine |
| CI | Jenkins 2.440.3 LTS, buildx 0.14+, Trivy 0.50+, hadolint/shellcheck, ruff 0.16.8, pytest 9.1.1 |
| Infra | Terraform 1.7+ (vpc ~> 5.8, eks ~> 20.17), Kind 0.22+, Kubernetes 1.29, Helm 3.14+, ingress-nginx 4.10.0 |
| GitOps / obs | ArgoCD v2.10.4+, kube-prometheus-stack ~58.0, Grafana v10.4+, Loki/Promtail v2.9+, Jaeger v1.57+ |

---

## 9. Troubleshooting

Full guide: [`troubleshooting.md`](troubleshooting.md) (connectivity and ARM64 deep dive).

| Symptom | Fix |
|---------|-----|
| Gateway returns 502 | A downstream isn't ready — `docker compose ps`, then `curl localhost:800x/health` |
| `pg_isready` keeps failing | `docker compose logs postgres`; wipe with `docker compose down -v` |
| `exec format error` on ARM | Rebuild both platforms: `docker buildx --platform linux/amd64,linux/arm64` |
| Order fails with 409 | Inventory `available` < requested — `POST /api/inventory/{id}/adjust` |
| `make ci-*` exits 3 | A required tool is missing on purpose — run `make doctor` |

---

Built local-first: if it doesn't run on a laptop, it doesn't ship.
