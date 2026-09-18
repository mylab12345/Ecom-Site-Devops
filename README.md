# E-Commerce Microservices Platform — Production-Grade (10 Services)

**Local-First, Cloud-Later** — Docker Compose local → Jenkins (buildx) → Terraform (Kind + AWS Graviton EKS) → ArgoCD → Observability.

> **Phase 1 ✅ COMPLETE** — All 10 microservices, Dockerfiles, docker-compose.yaml are production-ready. Phases 2-6 scaffolded below.

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
├── .env.example
├── Makefile                     # up / down / logs / health / test
├── README.md                    # you are here
├── troubleshooting.md           # connectivity & ARM64 deep dive
├── scripts/
│   ├── init-db.sql              # creates 9 DBs on postgres
│   ├── seed.sh                  # demo user + cart + order
│   └── test.sh                  # integration smoke tests (10 services)
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

## 5. Roadmap — Phases 2-6 (Local → AWS)

### Phase 2: Jenkins CI — Pipeline-as-Code + Multi-arch

**Deliverables (next):**
- `Jenkinsfile` (declarative, 3 stages: Lint/Test → docker buildx → Push to Docker Hub)
- `jenkins/setup.md` — install Jenkins LTS (2.440+), Docker, buildx, Docker Hub creds, webhook
- Multi-arch: `docker buildx build --platform linux/amd64,linux/arm64 -t $DOCKERHUB/ecom-$SVC:$TAG --push .` for all 10 services (parallel)
- Trivy scan gate
- Commit to bump `helm-charts/*/values.yaml` image tag (GitOps trigger)

**Local test:**
```bash
docker buildx create --name multi --use
docker buildx build --platform linux/amd64,linux/arm64 -f services/product/Dockerfile services/product --tag test:multi --load
```

### Phase 3: Infrastructure as Code — Terraform

```
terraform/
├── local-kind/
│   ├── main.tf      # kind cluster (1 control-plane, 2 workers), local registry mirror
│   ├── variables.tf
│   └── outputs.tf
└── aws-graviton/
    ├── main.tf      # EKS 1.29+, Graviton (m7g/m6g), spot instances (mixedInstancesPolicy), managed node groups ARM64, VPC, IRSA
    ├── variables.tf # graviton_instance_types = ["m7g.medium","m6g.medium"], spot = true, cost tags
    ├── eks.tf       # cluster, addons (vpc-cni, coredns, kube-proxy, ebs-csi)
    └── outputs.tf   # kubeconfig, cluster endpoint
```
- Versions: Terraform 1.7+, K8s 1.29+, Python 3.12
- Cost: spot + Graviton saves ~40% vs x86 on-demand. `capacity_type = SPOT`, `ami_type = AL2_ARM_64`.

### Phase 4: K8s Orchestration — Helm Charts

```
helm-charts/
├── ecom-common/           # helper templates
├── identity/templates/{deployment,service,ingress,networkpolicy,hpa,configmap}.yaml
├── product/ …
├── … (10 charts total)
├── ingress/values.yaml    # AWS ALB Ingress Controller / NGINX
└── networkpolicies/       # default-deny + allow gateway→services, services→db/redis/rabbitmq
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

## 6. Stable Versions (Phase 1)

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
|Terraform|1.7+ (planned)|
|K8s|1.29+ (planned)|
|Jenkins|2.440 LTS (planned)|

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
make up && make health && ./scripts/test.sh
```

Open: `http://localhost:8080/docs` (Gateway Swagger), `http://localhost:8080/health`, `http://localhost:15672` (RabbitMQ), `http://localhost:8001/docs` (direct).

**Phase 2 Jenkins guide coming next** — say the word and we scaffold `Jenkinsfile` + Terraform + Helm in one PR.

---

Made with ❤️ for Local-First DevOps. Graviton-ready.
