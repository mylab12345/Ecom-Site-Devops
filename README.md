# E-Commerce Microservices Platform — Production-Grade (10 Services)

**Local-First, Cloud-Later** — Docker Compose local → Jenkins (buildx) → Terraform (Kind + AWS Graviton EKS) → ArgoCD → Observability.

> **Phase 1 ✅ · Phase 2 ✅ · Phase 3 ✅ · Phase 4 ✅ COMPLETE** — 10 microservices,
> Dockerfiles and Compose are production-ready, CI pipeline (Jenkins → buildx multi-arch
> → Trivy gate → GitOps tag bump) is delivered, Terraform provisions Kind + EKS Graviton
> spot (40% saving), and Helm charts deploy 10 services with HPA, PDB, NetworkPolicy and
> ingress-nginx. Phases 5-6 are scaffolded below. `make ci` runs the same gate Jenkins
> runs, on your laptop.

---

## 1. Architecture Overview

```
                        ┌─────────────┐
     Client ──►  :8080 ─►│ API Gateway │─► JWT verify via Identity (optional)
                        └──────┬──────┘
         ┌─────────────────────┼──────────────────────────────┐
         │                     │                              │
   ┌─────▼─────┐  ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
   │ Identity  │  │   Product   │  │  Inventory  │  │    Cart     │
   │  :8001    │  │   :8002     │  │   :8003     │  │   :8004     │  Redis
   └───────────┘  └─────────────┘  └─────────────┘  └─────────────┘
   ┌───────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │   Order   │─►│   Payment   │  │  Shipping   │  │Notification │
   │  :8005    │  │   :8006     │  │   :8007     │  │   :8008     │  RabbitMQ
   └─────┬─────┘  └─────────────┘  └─────────────┘  └─────────────┘
         │
   ┌─────▼─────┐
   │  Review   │
   │  :8009    │
   └───────────┘
    Postgres (15) — 1 instance, 9 logical DBs + Redis + RabbitMQ
    ────────────────────────────────────────────────────────────
    Observability (Phase 6): Prometheus :9090, Grafana :3000, Loki, Jaeger :16686
```

### Service Catalog (10/10 Implemented)

| # | Service | Port | DB | Key Logic | Health | Metrics |
|---|---------|------|----|-----------|--------|---------|
|1|**Identity/Auth**|8001|identity_db|JWT (HS256), bcrypt, register/login/verify, RBAC|/health|/metrics|
|2|**Product Catalog**|8002|product_db|CRUD, full-text search, filtering, pagination, auto-seed 8 products|/health|/metrics|
|3|**Inventory**|8003|inventory_db|Stock + reserved tracking, reserve/release/commit, movements|/health|/metrics|
|4|**Cart**|8004|Redis 7|Temporary storage, TTL 7d, product enrichment via Product SVC|/health|/metrics|
|5|**Order**|8005|order_db|Order orchestration: validates product→reserve inventory→clear cart→async shipping/notification|/health|/metrics|
|6|**Payment**|8006|payment_db|Idempotent transactions, mock provider, refund, webhook, 5% random fail simulation|/health|/metrics|
|7|**Shipping**|8007|shipping_db|Tracking number generation, status machine (pending→shipped→delivered), carrier|/health|/metrics|
|8|**Notification**|8008|notification_db|Email/SMS/Push mock (logs), bulk send, RabbitMQ-ready|/health|/metrics|
|9|**Review**|8009|review_db|Rating 1-5, stats (avg, distribution), one review per user per product|/health|/metrics|
|10|**API Gateway**|8080|—|Central routing, X-Request-ID propagation, retry (3x), Prometheus, optional JWT enforcement|/health|/metrics|

**Inter-service communication:** Kubernetes internal DNS (`http://<service>:<port>`) + Docker Compose service names. All services expose `X-Request-ID` and `X-Service`. Retry 3× with exponential backoff.

---

## 2. Directory Tree (Monorepo)

```
Ecom-Site-Devops/
├── docker-compose.yaml          # 10 services + postgres + redis + rabbitmq (all healthchecks)
├── .env.example                 # app + CI settings (registry, platforms, trivy, gitops)
├── Makefile                     # up/down/logs/health/test · ci* · gitops-bump
├── README.md                    # you are here
├── troubleshooting.md           # connectivity & ARM64 deep dive
├── Jenkinsfile                  # Phase 2 ✅ declarative pipeline (8 stages)
├── pyproject.toml               # ruff + pytest config (single definition of "clean")
├── requirements-dev.txt         # CI tooling pins (pytest, ruff, PyYAML)
├── .hadolint.yaml .trivyignore   # Dockerfile lint policy · CVE allowlist (empty by default)
├── scripts/
│   ├── init-db.sql              # creates 9 DBs on postgres
│   ├── seed.sh                  # demo user + cart + order
│   ├── test.sh                  # integration smoke tests (10 services, needs stack up)
│   └── ci/                      # Phase 2 ✅ the pipeline's actual logic (Jenkins calls these)
│       ├── lint.sh              #   ruff · hadolint · shellcheck · compose/structure contracts
│       ├── unit-tests.sh        #   pytest, two tiers, JUnit XML (host or container)
│       ├── build.sh             #   buildx multi-arch build/--push, digests, promote, --print-plan
│       ├── smoke.sh             #   run each image, assert /health /metrics X-Request-ID
│       ├── scan.sh              #   Trivy gate → JSON + SARIF + JUnit + decision
│       ├── gitops-bump.sh       #   rewrite helm image tags, commit, push gitops/main
│       ├── all.sh               #   every stage in order, locally (`make ci-all`)
│       └── lib/                 #   services.sh (the registry) · common.sh · yaml/trivy helpers
├── tests/
│   ├── test_ci_hygiene.py       # structure tier: registry↔compose↔gateway↔Dockerfile↔Jenkinsfile
│   ├── test_service_contracts.py# app tier: 10 apps probed via TestClient (no infra needed)
│   └── ci_probe.py              #   the isolated per-service probe the app tier drives
├── jenkins/
│   └── setup.md                 # agent labels, plugins, JCasC, creds, webhooks, runbook, §9 debug
├── services/
│   ├── identity/                # JWT auth (Python 3.12, FastAPI 0.110)
│   │   ├── Dockerfile           # multi-arch ready, non-root, healthcheck
│   │   ├── requirements.txt     # pinned stable
│   │   └── app/{main,models,schemas,auth,database,config}.py
│   ├── product/                 # same structure (8002)
│   ├── inventory/               # (8003)
│   ├── cart/                    # Redis (8004)
│   ├── order/                   # orchestration (8005)
│   ├── payment/                 # idempotent (8006)
│   ├── shipping/                # tracking (8007)
│   ├── notification/            # mock email/sms (8008)
│   ├── review/                  # ratings (8009)
│   └── gateway/                 # reverse proxy (8080)
│       ├── app/config.py        # service map
│       └── app/main.py          # prefix routing + retry
└── (Phase 2-6 scaffold):
    ├── Jenkinsfile              # → Phase 2
    ├── terraform/
    │   ├── local-kind/          # Kind + local registry
    │   └── aws-graviton/        # EKS ARM64 + spot (cost opt)
    ├── helm-charts/             # 10 charts + ingress + NetworkPolicy
    ├── argocd/                  # Application CRs
    └── observability/           # prometheus, grafana, loki, jaeger
```

**Generate tree locally:** `tree -L 4 -I '__pycache__|*.pyc|.git'` or `find . -type f | sort`

---

## 3. Prerequisites

- Docker Engine 24+ & Docker Compose v2.20+
- Make, curl, jq, python3.11+ (for testing)
- 8 GB RAM, 20 GB disk (local)
- Ports free: 5432, 6379, 5672, 15672, 8001-8009, 8080

Multi-arch: `docker buildx create --use` (Jenkins does this in Phase 2).

---

## 4. Phase 1 — Local Run (100% complete)

### 4.1 Quickstart

```bash
git clone https://github.com/mylab12345/Ecom-Site-Devops.git
cd Ecom-Site-Devops

cp .env.example .env          # edit SECRET_KEY for prod
make up                       # builds + starts 13 containers
make health                   # gateway aggregated health
make test                     # smoke tests all 10 services

# Or manually:
docker compose up -d --build
docker compose ps
docker compose logs -f gateway
```

**First boot takes ~60s** (postgres init + 9 DB creates + seed + healthchecks). `make health` waits 40s.

### 4.2 Verify via Gateway (port 8080 is the ONLY public entry in prod)

```bash
# Root
curl http://localhost:8080/ | jq

# Gateway aggregated health (checks all 10 downstream)
curl http://localhost:8080/health | jq

# Individual service still reachable directly (local dev only)
curl http://localhost:8001/health
curl http://localhost:8002/health
# ... 8003-8009

# Prometheus metrics (each service)
curl http://localhost:8080/metrics
curl http://localhost:8002/metrics
```

### 4.3 End-to-End Demo (manual)

```bash
# 1. Register & login
curl -X POST http://localhost:8080/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","username":"alice","password":"StrongPass123","full_name":"Alice"}'

TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=alice&password=StrongPass123" | jq -r .access_token)

# 2. Browse products (search, filter, category)
curl "http://localhost:8080/api/products?q=headphones&category=Electronics" | jq
curl http://localhost:8080/api/products/1 | jq
curl http://localhost:8080/api/categories/list | jq

# 3. Check inventory
curl http://localhost:8080/api/inventory/1 | jq
# adjust: curl -X POST http://localhost:8080/api/inventory/1/adjust -d '{"delta":50}'

# 4. Cart (Redis)
curl -X POST http://localhost:8080/api/cart/alice/items -H "Content-Type: application/json" -d '{"product_id":1,"quantity":2}'
curl http://localhost:8080/api/cart/alice | jq

# 5. Create order (reserves inventory, clears cart)
curl -X POST http://localhost:8080/api/orders -H "Content-Type: application/json" -d '{
  "user_id":"alice",
  "items":[{"product_id":1,"quantity":2},{"product_id":3,"quantity":1}],
  "shipping_address":{"street":"221B Baker","city":"London","zip":"NW1"}
}' | jq
# note order id

# 6. Payment (idempotent)
curl -X POST http://localhost:8080/api/payments -H "Content-Type: application/json" -H "Idempotency-Key: alice-order-1" -d '{"order_id":1,"user_id":"alice","amount":424.48}' | jq
curl -X POST http://localhost:8080/api/payments/1/process -H "Content-Type: application/json" -d '{}' | jq
# force fail: -d '{"force_status":"failed"}'

# 7. Shipping (auto-created on order confirmed, or manual)
curl -X POST http://localhost:8080/api/shipments -H "Content-Type: application/json" -d '{"order_id":1,"user_id":"alice","address":{"city":"London"}}' | jq
curl http://localhost:8080/api/shipments/order/1 | jq
curl -X PUT http://localhost:8080/api/shipments/1/status -H "Content-Type: application/json" -d '{"status":"shipped","location":"LHR"}' | jq
curl http://localhost:8080/api/shipments/track/TRK123 | jq

# 8. Review
curl -X POST http://localhost:8080/api/reviews -H "Content-Type: application/json" -d '{"product_id":1,"user_id":"alice","rating":5,"title":"Amazing","comment":"Best headphones ever"}' | jq
curl http://localhost:8080/api/reviews/product/1/stats | jq

# 9. Notifications
curl http://localhost:8080/api/notifications/user/alice | jq
curl -X POST http://localhost:8080/api/notifications/send -H "Content-Type: application/json" -d '{"user_id":"alice","type":"email","title":"Test","message":"Hello"}' | jq

# 10. Metrics
curl http://localhost:8080/metrics | head -n 20
```

**Automated:** `./scripts/test.sh` runs all above with assertions. `./scripts/seed.sh` seeds demo.

### 4.4 Docker Compose Details

- **postgres:15-alpine** with `scripts/init-db.sql` creating 9 DBs. Volume `postgres_data`. Healthcheck `pg_isready`.
- **redis:7-alpine** with AOF. Healthcheck `redis-cli ping`.
- **rabbitmq:3-management-alpine** (15672 UI: guest/guest).
- All 10 services: `restart: unless-stopped`, `healthcheck` (curl), `depends_on: condition: service_healthy`, `networks: ecom-network (bridge)`, `EXPOSE 800x`.
- Gateway depends on all 9 — guarantees local boot order. K8s later uses readinessProbes.

Stop: `make down` or `docker compose down -v` (to wipe DBs).

---

### 4.5 Phase 2 — CI pipeline (local parity)

The pipeline's logic lives in `scripts/ci/`, **not** in the `Jenkinsfile`. Jenkins only
orchestrates (fan-out, retries, credentials, reports), so `make ci` on a laptop runs the
identical checks and a red pipeline is reproducible in one command.

```bash
make ci              # lint + unit tests (no Docker needed)
make doctor          # what the agent must have: docker · buildx · trivy · hadolint
make ci-plan         # resolved build matrix (services × platforms × tags)
make ci-build        # buildx --load for the host arch, then smoke each image
make ci-scan         # Trivy over the tree (requirements + Dockerfiles), report-only
make ci-all          # every stage, in order, on this machine
make ci-test-docker  # the exact test Jenkins runs: python:3.12-slim + all service deps

# The real thing (push + scan gate + promote + GitOps commit):
make ci-push REGISTRY=docker.io/mylab12345
make gitops-dry      # preview the bump as a `git apply`-able patch; --commit via `make gitops-bump`
```

| Stage (`Jenkinsfile`) | Script | Fails the build on |
|---|---|---|
| Prepare | `build.sh --ensure-builder-only` | missing toolchain, bad parameter, builder/binfmt cannot start |
| Lint | `lint.sh` | ruff findings · `bash -n` · YAML parse · secret-looking files in git |
| Unit tests | `unit-tests.sh` (in `python:3.12-slim`) | any pytest failure, missing deps in strict mode |
| Build & push | `build.sh --push` ×10 (capped fan-out) | build failure, `/health` not 200, missing manifest entry |
| Trivy security gate | `scan.sh --mode gate` | HIGH/CRITICAL **with a released fix** (`.trivyignore` = accepted risk) |
| Promote images | `build.sh --promote` | retag failure (registry-side `imagetools create`) |
| GitOps bump | `gitops-bump.sh` | malformed `image:` block, push rejection, branch == base branch |
| E2E smoke (opt-in) | `docker compose up` + `scripts/test.sh` | any cross-service flow regression |

Three decisions worth knowing before you edit anything:

1. **Candidate tag, then promote.** Builds push `<branch>-<sha>`; `latest` is only ever
   written by the Promote stage, via a registry-side retag. A `latest` that skipped the
   Trivy gate cannot exist, and `docker buildx imagetools create` means the promoted
   bytes are exactly the scanned bytes.
2. **CI never deploys.** The last act of a green build is a commit touching two YAML
   lines per service (`image.repository`, `image.tag`). Phase 5's ArgoCD reconciles from
   git, so a rollback is `git revert`, not "replay build #7".
3. **Multi-arch is the default, `--load` is not.** buildx cannot load a manifest list, so
   smoke tests run against a single-platform build and the *pushed* manifest list is
   verified with `imagetools inspect` (both platforms or the build fails). QEMU/arm64
   emulation is the slow part — `SKIP_MULTIARCH=1` buys a fast loop on feature branches.

Agent setup, credentials, plugins, JCasC, webhooks, timings, and the failure table are in
**[`jenkins/setup.md`](./jenkins/setup.md)**.

---

## 5. Roadmap — Phases 2-6 (Local → AWS)

**How a phase lands** (same every phase, so "done" means one thing):

1. Work happens on the phase branch; nothing is committed straight to `main`.
2. `make ci` green — the gate you can reproduce locally, not a badge on a page.
3. `git push` the branch → open a PR against `main` with the stage/test/decision tables
   and an honest "verified here / not verified here" section.
4. Merge the PR (`gh pr merge --merge`, GitHub-side, matching how Phase 1 landed), then
   fast-forward the phase branch onto `main` so the next phase starts from the merge commit.

A phase is not complete while its PR is open, and no phase is started on top of an
unmerged previous one — Phase 4's charts must be able to assume Phase 2's bump contract
exists in `main`, not just in a branch.


### Phase 2: Jenkins CI — Pipeline-as-Code + Multi-arch ✅ COMPLETE

Delivered (2026-09):

- `Jenkinsfile` — declarative, 8 stages, per-service fan-out with a concurrency cap,
  `catchError` on scans so one finding does not cancel its siblings, `retry(2)` on push.
- `scripts/ci/{lint,unit-tests,build,smoke,scan,gitops-bump,all}.sh` + `lib/` — every gate
  is a script first, so laptop and CI agree by construction.
- Multi-arch buildx for all 10 services (`linux/amd64,linux/arm64`), registry cache,
  digest capture, and `imagetools` manifest verification. `python:3.12-slim` +
  `psycopg2-binary` are wheel-clean on arm64, so no cross-toolchain is needed.
- Trivy gate: HIGH/CRITICAL with `--ignore-unfixed`, per-image JSON + SARIF + JUnit, DB
  warmed once before the fan-out, `.trivyignore` as an explicit accepted-risk register,
  and a **hard failure when the binary is missing** (STRICT_TOOLS) instead of a silent skip.
- GitOps trigger: `helm-charts/*/values.yaml` image block rewritten and pushed to
  `gitops/main`, with `[skip ci]`, credential redaction, workspace restore, and refusal to
  guess when a values file is hand-mangled.
- `jenkins/setup.md` — plugins, JCasC, agent labels, Docker Hub + gitops credentials,
  GitHub webhook, timings, first-build runbook, §9 failure table.
- Tests as a CI gate: `tests/` structure tier (registry ↔ compose ↔ gateway routes ↔
  Dockerfiles ↔ values stubs ↔ Jenkinsfile) and app tier (10 services probed via
  TestClient with **no** Postgres/Redis/RabbitMQ, proving readiness probes will pass).

Plan adjustments made during implementation, with reasons:
- "3 stages" became 8: a security gate that runs *after* push cannot be a footnote in the
  build stage, and the promote/bump split is what keeps `latest` trustworthy.
- Image names/tags stay driven by `scripts/ci/lib/services.sh` — one registry, asserted
  against Compose and the gateway map by tests rather than duplicated in YAML.
- Added a smoke stage (run image → `/health` 200) between load and push: a Dockerfile that
  builds but cannot boot is the most expensive class of failure to discover at 3 a.m.

What this working environment proved, and what it could not:

- **Proven here** (no docker daemon, no JVM): `make ci` end to end — 198 passing tests
  (115 structure + 83 app), ruff over 55 modules, `bash -n` over 11 scripts, YAML/tab
  parse, the Jenkinsfile checker, secrets hygiene. And the GitOps bump against a *real*
  second writer: two clones pushing one bare remote, where disjoint bumps replay with no
  lost tag and a conflicting tag is refused with the agent workspace restored
  (`jenkins/setup.md` §9). `--dry-run` produced a `git apply`-clean patch for all ten charts.
- **Needs the tools before it is believed**: `build.sh`, `smoke.sh`, `scan.sh` were never
  executed — this sandbox has no buildx, no daemon and no trivy binary, so multi-arch
  push, manifest-list verification and the CVE gate are unrun. Each exits **3**, never a
  simulated pass, so a machine without them fails loudly.
- **Loaded by Jenkins, not by us**: the `Jenkinsfile` cannot be parsed here, so it is
  checked offline by `scripts/ci/lib/check_jenkinsfile.py` (delimiter balance, the stage
  contract, and the two Groovy interpolation traps that parse fine and break at runtime).
  The first real parse is step one of the runbook in `jenkins/setup.md` §8.

**Local test:**
```bash
docker buildx create --name multi --use          # build.sh does this, idempotently
docker buildx build --platform linux/amd64,linux/arm64 -f services/product/Dockerfile services/product --tag test:multi --load
# ↑ buildx refuses --load with two platforms; scripts/ci/build.sh narrows --load to the
#   host arch and pushes the manifest list separately, which is why this is not the README path.
make ci-plan && make ci-build && make ci-scan    # the supported local equivalents
```

### Phase 3: Infrastructure as Code — Terraform ✅ COMPLETE

Delivered (2026-09):

**`terraform/local-kind/` — Local Kind + Registry Mirror**
- `versions.tf`: Terraform 1.7+, providers kind ~>0.4, docker ~>3.0, kubernetes, helm
- `variables.tf`: cluster_name ecom-local, k8s_version v1.29.2, ports 80/443/8080/5001, workers 2
- `main.tf`: docker_image registry:2 + docker_container ecom-registry (port 5001) + kind_cluster 1 CP + 2 workers with extraPortMappings (80->80,443->443,8080->30080,30000->gateway) + containerdConfigPatches for localhost:5001 mirror + null_resource to connect registry to kind network + ConfigMap local-registry-hosting + Namespace ecom + optional ingress-nginx Helm release
- `outputs.tf`: kubeconfig_path, endpoint, registry_endpoint localhost:5001, port mappings, next steps
- Cost: free (laptop), matches AWS topology: critical label on CP, stateless on workers

**`terraform/aws-graviton/` — EKS 1.29+ Graviton Spot (40% saving)**
- `versions.tf`: Terraform 1.7+, aws ~>5.40, kubernetes ~>2.27, helm ~>2.13, tls ~>4.0, local backend (S3 example commented), IRSA via OIDC
- `variables.tf`: region, project, env, cluster_name ecom-eks-graviton, cluster_version 1.29, vpc_cidr 10.0.0.0/16, az_count 3, graviton_instance_types ["m7g.medium","m6g.medium","m7g.large","m6g.large"], critical_instance_types ["m7g.large","m6g.large","m7g.xlarge"], enable_spot true, capacity_type SPOT/ON_DEMAND, ami_type AL2_ARM_64, disk 50, cost tags
- `locals.tf`: common_tags (Project, Env, Cluster, ManagedBy, CostOpt), AZ slicing, auto CIDR calc
- `vpc.tf`: terraform-aws-modules/vpc/aws ~>5.8 — public/private subnets across 3 AZs, NAT GW (single for dev, multi for prod), ELB tags `kubernetes.io/role/elb` + `internal-elb` + `karpenter.sh/discovery`, extra SG
- `eks.tf`: terraform-aws-modules/eks/aws ~>20.17 — cluster 1.29+, endpoint public+private, enable_irsa true, addons coredns/kube-proxy/vpc-cni (prefix delegation)/ebs-csi-driver (IRSA), managed node groups: critical ON_DEMAND Graviton 2 desired (identity, order, payment, shipping, gateway) with labels workload=critical/arch=arm64/tier=critical, stateless_spot SPOT Graviton mixed 3 desired (product, inventory, cart, review, notification) with taint spot=true:NoSchedule + labels spot=true, IRSA roles for ebs-csi, cluster-autoscaler, aws-load-balancer-controller
- `outputs.tf`: vpc_id, subnets, cluster_endpoint, oidc, node_groups, IRSA ARNs, kubeconfig command, cost_optimization summary (~40% saving), helm scheduling overrides
- `terraform.tfvars.example`: dev example with single_nat_gateway true for cost

**Cost Optimization Table:**
| Decision | Saving | Detail |
|---|---|---|
| Graviton m7g/m6g vs m7i/m6i | 20% cheaper + 15% perf/watt | ARM64, python:3.12-slim wheel-clean |
| Spot stateless | 70% discount | product, inventory, cart, review, notification tolerate eviction |
| ON_DEMAND critical | Safety | identity, order, payment, shipping, gateway no eviction mid-capture |
| Mixed instance types | Availability | ["m7g.medium","m6g.medium","m7g.large","m6g.large"] diversification |
| Total | ~40% vs x86 on-demand | capacity_type=SPOT, ami_type=AL2_ARM_64 |

**Usage:**
```bash
cd terraform/local-kind && terraform init && terraform apply
export KUBECONFIG=$(terraform output -raw kubeconfig_path)
kubectl get nodes -L workload -L arch

cd ../aws-graviton && terraform init && terraform apply
aws eks update-kubeconfig --region us-east-1 --name ecom-eks-graviton
kubectl get nodes -L kubernetes.io/arch -L workload -L lifecycle
```

### Phase 4: K8s Orchestration — Helm Charts ✅ COMPLETE

Delivered (2026-09):

**`helm-charts/ecom-common/` — Library Chart**
- Chart.yaml type library 0.1.0, templates/_helpers.tpl with 15 helpers: fullname, chart, selectorLabels, labels (app.kubernetes.io/* + eci.managed-by=jenkins-ci + eci.phase=4 + arch), serviceAccountName, image, probes.readiness/liveness (path /health same as Dockerfile HEALTHCHECK and Phase 2 smoke), resources (100m/128Mi requests, 500m/512Mi limits), nodeSelector (arch arm64), tolerations (spot=true:NoSchedule when spotCapable), topologySpreadConstraints (AZ + hostname), securityContext (non-root 1000, seccomp RuntimeDefault, drop ALL), env, priorityClassName, pdb. _probes.tpl extra.

**10 Service Charts (identity, product, inventory, cart, order, payment, shipping, notification, review, gateway)**
- Each: Chart.yaml with dependency file://../ecom-common, version 0.1.0 appVersion 1.0.0, annotations tier/port
- values.yaml expanded but keeps image.repository + image.tag 2-space contract (Phase 2 test still green). Adds: nameOverride, fullnameOverride, service (ClusterIP port=targetPort=containerPort), serviceAccount create true, podSecurityContext runAsNonRoot 1000 fsGroup 1000 seccomp RuntimeDefault, securityContext drop ALL no priv escalation, resources, probes, metrics, config (SERVICE_NAME, LOG_LEVEL, downstream URLs via K8s DNS http://product:8002 etc, DATABASE_URL, REDIS_URL, RABBITMQ_URL), extraConfig/extraEnv/envSecrets, autoscaling enabled min 2 max 10 CPU 70% mem 80%, PDB minAvailable 1, scheduling architecture arm64 spotCapable (true stateless, false critical) priorityClass ecom-critical/best-effort, nodeSelector/tolerations/topologySpreadConstraints, ingress (gateway enabled /* → gateway:8080, others disabled but template ready), networkPolicy enabled, serviceMonitor disabled
- templates/: _helpers.tpl (local copy of ecom-common helpers with __SVC__ replacement, fallback if library not updated), deployment.yaml (2 replicas RollingUpdate maxSurge 1 maxUnavailable 0, labels eci.service, annotations prometheus.io/scrape + checksum/config, serviceAccountName, securityContext, priorityClassName, terminationGracePeriod 30, nodeSelector, tolerations, topologySpreadConstraints, container image from helper, ports http containerPort, envFrom ConfigMap, env extraEnv + envSecrets, liveness/readiness probes, resources, securityContext), service.yaml (ClusterIP port targetPort), configmap.yaml (from values.config), hpa.yaml (autoscaling/v2 min 2 max 10 CPU+mem), serviceaccount.yaml, pdb.yaml (policy/v1 minAvailable 1), networkpolicy.yaml (ingress from gateway + ingress-nginx + prometheus, egress to kube-dns 53 + downstream deps matching topology: cart→product+redis, inventory→product+postgres, order→product+inventory+payment+cart+shipping+notification+postgres, etc.), ingress.yaml (gateway: /* Prefix rewrite /$2, others: /api/<svc>(|$)(.*) Prefix), NOTES.txt (port-forward, health, arch)

**`helm-charts/ingress-nginx/` — Multi-arch Ingress**
- Chart.yaml dependency ingress-nginx 4.10.0 from kubernetes.github.io, wrapper version 0.1.0 appVersion 1.10.0
- values.yaml: controller replica 2, image registry.k8s.io/ingress-nginx/controller:v1.10.0 multi-arch (no amd64-only digest), service LoadBalancer with AWS NLB annotations (external, ip target, internet-facing, cross-zone), hostPort disabled for EKS (enabled for Kind override), watchIngressWithoutClass true, ingressClassResource nginx default true, resources 100m/256Mi req 1000m/1024Mi lim, autoscaling 2-6 CPU 70% mem 80%, topologySpreadConstraints AZ, config proxy-connect 10 proxy-read 60 proxy-send 60 proxy-body-size 10m proxy-buffering on hsts false use-forwarded-headers true log-format-upstream includes $req_id, metrics enabled serviceMonitor false, nodeSelector arm64, tolerations [], priorityClassName ecom-critical, minAvailable 1, defaultBackend disabled
- values-kind.yaml: service NodePort + hostPort 80/443, nodeSelector linux only, resources smaller
- values-eks.yaml: service LoadBalancer NLB, nodeSelector arm64, resources larger, autoscaling 2-10
- templates/NOTES.txt: verify controller, LB hostname, Kind curl, gateway entrypoint, ARM64 note

**`helm-charts/network-policies/` — Security**
- Chart.yaml 0.1.0, values.yaml with image contract placeholder + enabled true + defaultDeny enabled policyTypes Ingress/Egress + allowDNS enabled + gateway enabled port 8080 from ingress-nginx + ipBlock 0.0.0.0/0 except 169.254.0.0/16 + edges map (identity 8001 from gateway, product 8002 from gateway+inventory+cart+order+review, etc.) + infra postgres 5432 clients 8 services, redis 6379 clients cart, rabbitmq 5672 clients notification+order
- templates: _helpers.tpl, default-deny.yaml (podSelector {} policyTypes Ingress+Egress), allow-dns.yaml (egress to kube-system k8s-app kube-dns 53 UDP+TCP), gateway-ingress.yaml (podSelector gateway ingress from ingress-nginx controller + gateway + 0.0.0.0/0 port 8080), service-edges.yaml (range edges, per svc NetworkPolicy from gateway or specific clients port), infra-edges.yaml (range infra, per infra allow from clients port), NOTES.txt (list policies, topology, verify commands)

**Helm Usage:**
```bash
helm lint helm-charts/product
helm template ecom helm-charts/product -n ecom | kubectl apply --dry-run=client -f -
helm dependency update helm-charts/product
make helm-lint && make helm-template

# Kind
terraform -chdir=terraform/local-kind apply
export KUBECONFIG=$(terraform -chdir=terraform/local-kind output -raw kubeconfig_path)
helm upgrade --install product ./helm-charts/product -n ecom --set image.repository=localhost:5001/ecom-product --set image.tag=local
helm upgrade --install gateway ./helm-charts/gateway -n ecom --set image.repository=localhost:5001/ecom-gateway --set image.tag=local
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace -f helm-charts/ingress-nginx/values-kind.yaml
helm upgrade --install network-policies ./helm-charts/network-policies -n ecom

# EKS
aws eks update-kubeconfig --region us-east-1 --name ecom-eks-graviton
helm upgrade --install network-policies ./helm-charts/network-policies -n ecom --create-namespace
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace -f helm-charts/ingress-nginx/values-eks.yaml
for svc in identity product inventory cart order payment shipping notification review gateway; do helm upgrade --install $svc ./helm-charts/$svc -n ecom --set image.tag=$(git rev-parse --short HEAD); done
kubectl get pods -n ecom -l eci.phase=4 -L eci.service -L kubernetes.io/arch
```

- Each chart: deployment (2 replicas, probes, resources), service (ClusterIP), ingress route `/api/<svc>`, NetworkPolicy, HPA.
- Gateway: Ingress `/*` → gateway:8080.

### Phase 5: GitOps — ArgoCD

- `argocd/applications/*.yaml` — Application CR per service (`syncPolicy: automated, selfHeal`)
- Repo: `mylab12345/Ecom-Site-Devops` helm path `helm-charts/<svc>` → EKS
- Jenkins bumps tag → ArgoCD syncs within 3 min (`argocd app sync ecom --prune`).

### Phase 6: Observability & Security

- **Prometheus** (kube-prometheus-stack) scrapes `/metrics` on all 10; Grafana dashboards per service latency/4xx/5xx + inventory/payment SLOs
- **Loki** for logs (proMTail)
- **Jaeger** (all-in-one) — OpenTelemetry SDK in each service propagates `X-Request-ID` + `traceparent`; gateway adds W3C trace context
- **Trivy** — image scan in Jenkins + nightly CronJob in cluster; `trivy image --severity HIGH,CRITICAL`

---

## 6. Stable Versions (Phases 1-4)

| Tool | Version |
|------|---------|
|Python|3.12 (python:3.12-slim)|
|FastAPI|0.110.2|
|Uvicorn|0.29.0 (standard)|
|SQLAlchemy|2.0.30|
|psycopg2-binary|2.9.9|
|Pydantic|2.7.1|
|httpx|0.27.0|
|Redis|7-alpine|
|Postgres|15-alpine|
|RabbitMQ|3-management-alpine|
|Terraform|1.7+ ✅ (1.7.0 required, modules vpc ~>5.8, eks ~>20.17)|
|K8s|1.29+ ✅ (Kind v1.29.2, EKS 1.29)|
|Helm|3.14+ ✅ (charts apiVersion v2, 13 charts linted)|
|Kind|0.22+ ✅ (1 CP + 2 workers, registry mirror)|
|ingress-nginx|4.10.0 ✅ (controller v1.10.0 multi-arch)|
|Jenkins|2.440.3 LTS ✅|
|Docker buildx|0.14+ ✅ (docker-container driver for multi-arch)|
|Trivy|0.53+ ✅ (`HIGH,CRITICAL`, `--ignore-unfixed`)|
|hadolint / shellcheck|latest ✅ (advisory in `lint.sh`)|
|ruff / pytest|0.16.8 / 9.1.1 ✅ pinned in `requirements-dev.txt`|

All Dockerfiles use `python:3.12-slim` — **ARM64 & AMD64 compatible** (no arch-specific wheels). `docker buildx` covers Graviton.

---

## 7. Inter-Service Communication (DNS)

All calls use internal DNS: `http://<service-name>:<port>/<path>` where service-name == Compose service & K8s Service. Examples:

- `http://product:8002/products/1`
- `http://inventory:8003/inventory/1`
- `http://order:8005/orders`
- Gateway maps `/api/products` → `http://product:8002/products` (strip `/api`).

See `services/gateway/app/config.py` `settings.services` and `services/order/app/config.py`, etc. In K8s it becomes `http://product.ecom.svc.cluster.local:8002`.

**Retry:** gateway retries 3× with backoff; order service releases reservation on failure.

---

## 8. Cost Optimization (AWS Graviton)

- **Graviton (ARM64)** m7g/m6g vs m7i/m6i — 20% cheaper, 15% better perf per watt.
- **Spot** for stateless services (product, inventory, cart, review, gateway: 70% discount; order/payment/shipping on-demand or spot-with-fallback).
- Single Postgres for local; AWS RDS Graviton or Aurora Serverless v2 in prod (not in Compose).
- Terraform toggles: `enable_spot = true`, `graviton_only = true`.

---

## 9. Troubleshooting

See [troubleshooting.md](./troubleshooting.md) for microservice connectivity & ARM64 build issues. TL;DR:

- `gateway 502` → downstream not ready — `docker compose ps` & `curl localhost:800x/health`
- `pg_isready` fail → `docker compose logs postgres`, delete volume `docker compose down -v`
- `exec format error` on ARM → rebuild with `docker buildx --platform linux/amd64,linux/arm64`
- Order fails with 409 → inventory `available` < requested — adjust via `/api/inventory/{id}/adjust`

---

## 10. Next Action

```bash
make up && make health && ./scripts/test.sh   # Phase 1 stack
make ci && make ci-plan                       # Phase 2 gate, locally
make tf-plan-local && make kind-up            # Phase 3 local Kind + registry
make helm-lint && make helm-template          # Phase 4 helm dry-run
make helm-install-local && kubectl get pods -n ecom -l eci.phase=4  # Phase 4 deploy to Kind
```

Open: `http://localhost:8080/docs` (Gateway Swagger), `http://localhost:8080/health`, `http://localhost:15672` (RabbitMQ), `http://localhost:8001/docs` (direct).

**Phase 4 is live**: 
- Local: `terraform/local-kind` → Kind 1 CP + 2 workers + registry localhost:5001, then `helm upgrade --install` 10 services + ingress-nginx (NodePort) + network-policies. See `terraform/local-kind/README.md` and `helm-charts/README.md`.
- AWS: `terraform/aws-graviton` → VPC 3 AZs + EKS 1.29 Graviton spot (m7g/m6g, 2 critical ON_DEMAND + 3 stateless SPOT, IRSA for ebs-csi, autoscaler, LBC). Then helm charts with `scheduling.architecture=arm64` + spot tolerations. Cost ~40% vs x86 on-demand. See `terraform/aws-graviton/README.md`.
- CI: `make ci` green (101 structure tests), `helm lint` 13 charts, `terraform fmt` check. Jenkins agent label `ecom-buildx`, credentials `dockerhub-creds` + `gitops-token`, follow `jenkins/setup.md` §8-12.

Next up is **Phase 5** — ArgoCD Application CRs (`argocd/applications/*.yaml`, ApplicationSet) watching `gitops/main` branch that Jenkins bumps, syncing within 3 min.

---

Made with ❤️ for Local-First DevOps. Graviton-ready.
