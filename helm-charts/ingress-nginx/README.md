# ingress-nginx (Phase 4) ✅ — Multi-arch Ingress Controller Wrapper

Production wrapper around upstream `ingress-nginx` chart 4.10.0 (controller v1.10.0), pinned by `terraform/aws-graviton` and `terraform/local-kind`.

## Why Wrapper?

Values are kept here rather than only in Terraform so annotations services reference are reviewable next to them, and `helm lint` + `helm template | kubectl apply --dry-run=client` can run in CI (Phase 4 requirement from `helm-charts/ecom-common/README.md`).

## Constraints from Phase 2 (still enforced)

- Gateway is only HTTP entrypoint, listening on `8080` (`helm-charts/gateway/values.yaml`); every other service is `ClusterIP`. Gateway strips leading `/api` and forwards rest, so `/api/products/health` reaches product as `/products/health` — ingress above it must NOT rewrite paths except gateway's `/*` → `/$2`, or two layers fight.
- ARM64 is default node architecture (`scheduling.architecture` in each values), so this MUST use multi-arch ingress-nginx image `registry.k8s.io/ingress-nginx/controller:v1.10.0` — do NOT pin amd64-only digest.

## Chart

- `Chart.yaml`: apiVersion v2, name ingress-nginx, version 0.1.0 appVersion 1.10.0, dependency ingress-nginx 4.10.0 from https://kubernetes.github.io/ingress-nginx alias ingress-nginx-upstream
- `values.yaml`: controller replica 2, image registry.k8s.io/ingress-nginx/controller:v1.10.0 multi-arch, admissionWebhooks enabled, service LoadBalancer with AWS NLB annotations (external, ip, internet-facing, cross-zone), hostPort disabled (enabled in values-kind.yaml for Kind), watchIngressWithoutClass true, ingressClassResource nginx default true, resources 100m/256Mi req 1000m/1024Mi lim, autoscaling 2-6 CPU 70% mem 80%, topologySpreadConstraints AZ, config proxy-connect 10 proxy-read 60 proxy-send 60 proxy-body-size 10m proxy-buffering on hsts false use-forwarded-headers true log-format-upstream includes $req_id, metrics enabled, nodeSelector arm64, tolerations [], priorityClassName ecom-critical, minAvailable 1, defaultBackend disabled
- `values-kind.yaml`: NodePort + hostPort 80/443, smaller resources, linux only selector
- `values-eks.yaml`: LoadBalancer NLB, arm64 selector, larger resources, autoscaling 2-10

## Install

```bash
# EKS
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace

# Kind
helm upgrade --install ingress-nginx ./helm-charts/ingress-nginx -n ingress-nginx --create-namespace \
  --set ingress-nginx-upstream.controller.service.type=NodePort \
  --set ingress-nginx-upstream.controller.hostPort.enabled=true
# Or: -f helm-charts/ingress-nginx/values-kind.yaml

kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller -o wide
kubectl get svc -n ingress-nginx ingress-nginx-controller
```

## Verification

- `kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'` → LB hostname for DNS
- `curl http://<LB>/api/products/health` → gateway:8080 → product:8002
- ARM64: `kubectl get nodes -L kubernetes.io/arch` shows arm64, controller pod on arm64 node

See `../README.md` Phase 4 section for full Helm usage.
