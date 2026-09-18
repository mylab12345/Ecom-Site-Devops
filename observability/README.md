# Observability & Security (Phase 6) ✅ COMPLETE

Production-grade observability and security suite for the 10 e-commerce microservices,
providing full-stack visibility across Metrics (Prometheus + Grafana), Logs (Loki + Promtail),
Traces (Jaeger + OpenTelemetry), and In-Cluster Security (Trivy + Pod Security Standards).
Optimized for AWS Graviton (ARM64) and local Kind clusters.

---

## 1. Architecture Overview

```
                                      Ingress Traffic (HTTP / W3C Trace Context)
                                                        │
                                                        ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                           ecom Namespace                                               │
│                                                                                                        │
│   ┌───────────────┐     W3C traceparent     ┌───────────────┐     W3C traceparent    ┌───────────────┐ │
│   │    Gateway    │────────────────────────►│  Order / Cart │───────────────────────►│    Payment    │ │
│   └───────┬───────┘                         └───────┬───────┘                        └───────┬───────┘ │
│           │                                         │                                        │         │
│           │ /metrics                                │ /metrics                               │ /metrics│
└───────────┼─────────────────────────────────────────┼────────────────────────────────────────┼─────────┘
            │                                         │                                        │
            ▼                                         ▼                                        ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       observability Namespace                                          │
│                                                                                                        │
│  ┌─────────────────────────┐          ┌─────────────────────────┐          ┌────────────────────────┐  │
│  │       Prometheus        │          │      Loki + Promtail    │          │  Jaeger (All-in-One)   │  │
│  │ (kube-prometheus-stack) │          │    (Log Aggregation)    │          │  (OTLP Trace Spans)    │  │
│  └────────────┬────────────┘          └────────────┬────────────┘          └───────────┬────────────┘  │
│               │                                    │                                   │               │
│               └──────────────────────────┬─────────┴───────────────────────────────────┘               │
│                                          ▼                                                             │
│                               ┌─────────────────────┐                                                  │
│                               │       Grafana       │                                                  │
│                               │ Dashboards & Alerts │                                                  │
│                               └─────────────────────┘                                                  │
│                                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Nightly Security: Trivy CronJob (aquasec/trivy:0.50.0) scans ecom namespace pods daily at 02:00  │  │
│  └──────────────────────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Directory Structure

```
observability/
├── README.md                                 # Full documentation & runbook (this file)
├── prometheus/
│   ├── kube-prometheus-stack-values.yaml     # Production values (ARM64 Graviton, 15d retention, scrape configs)
│   ├── rules/
│   │   └── ecom-alerts.yaml                  # PrometheusRule CR (5xx rates, p99 latency, payment alerts)
│   └── servicemonitors/
│       └── ecom-servicemonitors.yaml         # ServiceMonitor CR for 10 microservices
├── grafana/
│   ├── datasources.yaml                      # Auto-provisioned datasources (Prometheus, Loki, Jaeger)
│   ├── dashboards-provisioning.yaml          # Dashboard provider ConfigMap
│   └── dashboards/
│       ├── ecom-overview.json                # Global platform KPIs (RPS, 5xx %, Latency, Pod health)
│       ├── service-detail.json               # Per-service drill-down ($service variable, endpoint metrics)
│       └── ecom-business-slo.json            # Business SLOs (checkout rate, payment success 99.9%)
├── loki/
│   ├── loki-values.yaml                      # Loki Helm values (TSDB schema v13, 7d retention)
│   └── promtail-values.yaml                  # Promtail DaemonSet (structured CRI parsing, X-Request-ID extraction)
├── jaeger/
│   ├── jaeger-all-in-one.yaml                # Jaeger all-in-one Deployment + Service (UI 16686, OTLP 4317/4318)
│   └── otel-collector-config.yaml            # OpenTelemetry Collector configuration
└── security/
    ├── trivy-cronjob.yaml                    # Nightly Trivy cluster scan CronJob (0 2 * * *)
    ├── rbac.yaml                             # RBAC for Trivy scanner ServiceAccount
    └── pod-security-standards.yaml           # Pod Security Standards baseline / restricted namespace labels
```

---

## 3. Metrics & Alerting (Prometheus)

All 10 services expose standard Prometheus metrics at `/metrics`:
- `http_requests_total{method, endpoint, status}`: Total HTTP requests counter
- `http_request_duration_seconds{endpoint}`: Latency histogram for percentiles (p50, p90, p99)
- `orders_created_total`: Business event counter
- In addition to standard Python / process metrics.

### Key Prometheus Alert Rules (`rules/ecom-alerts.yaml`)
| Alert | Condition | Severity | Description |
|---|---|---|---|
| `EcomServiceHighErrorRate` | 5xx rate > 1% over 5m | critical | Service returning server errors |
| `EcomServiceHighLatencyP99` | p99 latency > 1.0s over 5m | warning | Degraded user experience |
| `EcomServiceInstanceDown` | `up == 0` for 2m | critical | Pod unhealthy or unresponsive |
| `EcomPaymentFailureSpike` | Payment 5xx rate > 0.1/s | critical | Critical checkout failure |
| `EcomPodCrashLooping` | Restarts > 3 in 15m | critical | Container crashing repeatedly |

---

## 4. Grafana Dashboards

Grafana dashboards are provisioned automatically via ConfigMaps:

1. **`E-Commerce Platform Overview` (`ecom-overview.json`)**:
   - High-level executive and operations view: Total RPS, error rate percentage, p99 latency, and active pod counts.
   - Status code breakdown (2xx vs 4xx vs 5xx), per-service throughput, and pod memory working sets.
2. **`E-Commerce Microservice Deep-Dive` (`service-detail.json`)**:
   - Parameterized dashboard with a `$service` dropdown (`identity`, `product`, `order`, etc.).
   - Per-endpoint latency percentiles (p50, p95, p99), endpoint RPS, CPU cores, and memory footprints.
3. **`E-Commerce Business & SLO Dashboard` (`ecom-business-slo.json`)**:
   - Tracks business metrics against SLA/SLO agreements:
     - **Payment Success Rate SLO**: Target 99.9%
     - **Availability SLO**: Target 99.95%
     - Order creation velocity (/min) and payment status distribution.

---

## 5. Log Aggregation (Loki & Promtail)

- **Promtail** runs as a DaemonSet on every node (including Spot instances with appropriate tolerations).
- Scrapes container logs from `/var/log/pods/ecom_*/*/*.log`.
- Pipeline stages extract structured fields matching Python logging format:
  `"%(asctime)s %(levelname)s %(name)s %(message)s"`
- Extracted labels: `service`, `level`, `request_id`, `pod`, `namespace`.

### Sample LogQL Queries
```logql
# Filter error logs across all services
{namespace="ecom"} |= "ERROR"

# Track logs for a specific request ID across all hops
{namespace="ecom"} |= "X-Request-ID" | json | request_id="3f8471c2-..."

# Count errors per minute for the payment service
sum(rate({namespace="ecom", service="payment-service"} |= "ERROR" [1m])) by (pod)
```

---

## 6. Distributed Tracing (Jaeger & OpenTelemetry)

- Microservices propagate the **W3C Trace Context** (`traceparent`, `tracestate`) and `X-Request-ID` across HTTP hops.
- Gateway receives inbound requests, generates a trace ID if absent, and stamps headers on downstream calls to `order`, `product`, `payment`, etc.
- Jaeger All-in-One ingests OTLP traces directly on gRPC (port `4317`) and HTTP (port `4318`).
- Jaeger Query UI is accessible on port `16686`.

---

## 7. Cluster Security (Trivy & Pod Security Standards)

### Nightly Trivy Scan CronJob
- Scheduled at `02:00 UTC` daily via `observability/security/trivy-cronjob.yaml`.
- Uses `aquasec/trivy:0.50.0` running in-cluster with read-only RBAC.
- Executes `trivy k8s --namespace ecom --severity HIGH,CRITICAL --ignore-unfixed`.
- Scans all running container images and workloads, surfacing CVEs and misconfigurations.

### Pod Security Standards
- Namespace `ecom` enforces the Kubernetes `baseline` profile and audits against the `restricted` profile.
- All service Dockerfiles run as non-root UID 1000, drop ALL Linux capabilities, and disable privilege escalation.

---

## 8. Deployment & Usage

### 8.1 Deploying the Observability Stack

```bash
# 1. Create namespace
kubectl create namespace observability

# 2. Deploy Jaeger All-in-One
kubectl apply -f observability/jaeger/jaeger-all-in-one.yaml

# 3. Deploy Prometheus & Grafana via Helm
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n observability \
  -f observability/prometheus/kube-prometheus-stack-values.yaml

# 4. Apply custom Alert Rules and ServiceMonitors
kubectl apply -f observability/prometheus/rules/ecom-alerts.yaml
kubectl apply -f observability/prometheus/servicemonitors/ecom-servicemonitors.yaml

# 5. Provision Grafana Datasources and Dashboards
kubectl apply -f observability/grafana/datasources.yaml
kubectl apply -f observability/grafana/dashboards-provisioning.yaml

# 6. Deploy Loki & Promtail
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
helm upgrade --install loki grafana/loki -n observability -f observability/loki/loki-values.yaml
helm upgrade --install promtail grafana/promtail -n observability -f observability/loki/promtail-values.yaml

# 7. Apply Security Standards & Trivy Nightly Scanner
kubectl apply -f observability/security/pod-security-standards.yaml
kubectl apply -f observability/security/rbac.yaml
kubectl apply -f observability/security/trivy-cronjob.yaml
```

### 8.2 Accessing UIs

```bash
# Grafana (admin / ecom-grafana-secure-admin)
kubectl port-forward -n observability svc/kube-prometheus-stack-grafana 3000:80

# Prometheus UI
kubectl port-forward -n observability svc/kube-prometheus-stack-prometheus 9090:9090

# Jaeger Query UI
kubectl port-forward -n observability svc/jaeger-query 16686:16686

# Trigger immediate manual Trivy security scan
kubectl create job --from=cronjob/trivy-nightly-cluster-scan trivy-manual-scan -n observability
kubectl logs -n observability job/trivy-manual-scan -f
```
