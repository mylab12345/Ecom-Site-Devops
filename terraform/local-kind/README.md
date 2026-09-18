# Local Kind Terraform (Phase 3) — 1 Control-Plane + 2 Workers + Local Registry Mirror

Local Kubernetes for Phase 4 Helm chart development, matching the AWS Graviton topology but running on your laptop via Kind.

## Architecture

```
Kind Cluster: ecom-local (v1.29.2)
├── control-plane (1)
│   ├── extraPortMappings: 80->80, 443->443, 8080->30080, 30000->gateway NodePort
│   ├── labels: ingress-ready=true, workload=critical
│   └── mounts: /tmp
├── workers (2)
│   ├── labels: workload=stateless, arch=amd64|arm64
│   └── mounts: /tmp
├── containerdConfigPatches: registry mirror localhost:5001 -> ecom-registry:5000
└── Local Registry: ecom-registry:5000 (Docker container registry:2)
    └── volume: ecom-registry-data
```

## Files

| File | Purpose |
|------|---------|
| `versions.tf` | Terraform 1.7+, providers: kind ~>0.4, docker ~>3.0, kubernetes, helm |
| `variables.tf` | Tunables: cluster_name, k8s_version, ports, workers, registry |
| `main.tf` | Registry container + Kind cluster with port mappings + registry mirror + ecom namespace + optional ingress-nginx |
| `outputs.tf` | kubeconfig, endpoints, next steps |

## Why Local Registry?

- **Speed:** `docker buildx --push localhost:5001/ecom-product:tag` is instant, no DockerHub rate limits
- **Parity:** Same flow as Jenkins: build → push → scan → gitops bump (but local)
- **Kind integration:** Kind nodes configured via `containerdConfigPatches` to use `ecom-registry:5000` as mirror for `localhost:5001`
- **Docs:** https://kind.sigs.k8s.io/docs/user/local-registry/

## Usage

```bash
cd terraform/local-kind

# 1. Init
terraform init

# 2. Apply (creates registry + kind cluster)
terraform apply

# 3. Kubeconfig
export KUBECONFIG=$(terraform output -raw kubeconfig_path)
kubectl cluster-info
kubectl get nodes -o wide -L workload -L arch

# 4. Verify registry connectivity
docker ps | grep ecom-registry
curl http://localhost:5001/v2/_catalog

# 5. Build and push images to local registry
cd ../..
make ci-build REGISTRY=localhost:5001 IMAGE_TAG=local
make ci-push REGISTRY=localhost:5001 IMAGE_TAG=local
# Or directly:
# docker buildx build --platform linux/amd64 -f services/product/Dockerfile services/product -t localhost:5001/ecom-product:local --push

# 6. Deploy a service via Helm (Phase 4)
helm upgrade --install product ./helm-charts/product \
  --namespace ecom --create-namespace \
  --set image.repository=localhost:5001/ecom-product \
  --set image.tag=local

kubectl get pods -n ecom
kubectl port-forward -n ecom svc/product 8002:8002 &
curl http://localhost:8002/health

# 7. Deploy all via ArgoCD or loop
for svc in identity product inventory cart order payment shipping notification review gateway; do
  helm upgrade --install $svc ./helm-charts/$svc -n ecom --set image.repository=localhost:5001/ecom-$svc --set image.tag=local
done

# 8. Install ingress-nginx (optional, via terraform var or helm)
terraform apply -var="install_ingress_nginx=true"
# Or:
# helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
# helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace \
#   --set controller.hostPort.enabled=true --set controller.service.type=NodePort

# 9. Destroy
terraform destroy
# Manual cleanup if needed:
# kind delete cluster --name ecom-local
# docker rm -f ecom-registry
# docker volume rm ecom-registry-data
```

## Port Mappings

| Host Port | Container Port | Purpose |
|-----------|---------------|---------|
| 80 | 80 | Ingress HTTP |
| 443 | 443 | Ingress HTTPS |
| 8080 | 30080 | Gateway via NodePort (if gateway chart uses NodePort 30080) |
| 8080 (alt) | 30000 | Gateway direct NodePort |
| 30080, 30443 | 30080, 30443 | Extra NodePorts |
| 5001 | 5000 | Local registry |

Customize via `extra_port_mappings` variable.

## Comparison to AWS Graviton

| Aspect | local-kind | aws-graviton |
|--------|------------|--------------|
| Nodes | 1 CP + 2 workers (Docker) | 2 critical ON_DEMAND + 3 stateless SPOT (EC2 Graviton) |
| Registry | localhost:5001 (registry:2) | DockerHub or ECR |
| CNI | kindnet | vpc-cni with prefix delegation |
| Storage | local-path (default) | ebs-csi-driver with IRSA |
| Ingress | ingress-nginx NodePort + hostPort | ALB or NLB via aws-lbc |
| Cost | Free (laptop) | ~$265/mo dev |
| Purpose | Phase 4 Helm dev + CI smoke | Phase 3+ prod-like |

## Troubleshooting

- `kind cluster creation fails` → Check Docker running, `docker ps`, free ports 80,443,8080,5001
- `registry not reachable from kind nodes` → `docker network connect kind ecom-registry` (terraform does this via null_resource)
- `ImagePullBackOff` → Ensure image pushed to `localhost:5001/ecom-xxx:tag` and kind config has mirror
- `port already allocated` → Change `http_port`, `https_port`, `gateway_port` vars
- `terraform apply fails on kubernetes provider` → Kind cluster not ready yet; run `terraform apply` again (depends_on should handle)

## Makefile Integration (optional)

Add to root Makefile:

```makefile
kind-up:
	cd terraform/local-kind && terraform init && terraform apply -auto-approve

kind-down:
	cd terraform/local-kind && terraform destroy -auto-approve

kind-kubeconfig:
	@echo "export KUBECONFIG=$$(cd terraform/local-kind && terraform output -raw kubeconfig_path)"
```

## Next: Phase 4

Once Kind is up, Phase 4 Helm charts can be deployed:

```bash
helm template ecom ./helm-charts/product -n ecom --set image.tag=local | kubectl apply --dry-run=client -f -
helm upgrade --install ecom-product ./helm-charts/product -n ecom
```

See `../../helm-charts/README.md` for chart structure.
