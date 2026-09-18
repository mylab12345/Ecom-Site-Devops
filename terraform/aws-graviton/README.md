# AWS Graviton Terraform (Phase 3) — EKS 1.29+ on ARM64 Spot

Production-grade Terraform for **EKS on Graviton (m7g/m6g) with Spot cost optimization**, saving ~40% vs x86 on-demand.

## Architecture

```
VPC (10.0.0.0/16) across 3 AZs
├── Public Subnets (ALB, NAT GW) — tags: kubernetes.io/role/elb
├── Private Subnets (EKS nodes) — tags: kubernetes.io/role/internal-elb + karpenter.sh/discovery
└── EKS 1.29+
    ├── Managed Node Group: critical (ON_DEMAND, Graviton)
    │   └── identity, order, payment, shipping, gateway (2 replicas, PDB, no spot taint)
    ├── Managed Node Group: stateless-spot (SPOT, Graviton, mixedInstances diversification)
    │   └── product, inventory, cart, review, notification (3 replicas, spot toleration)
    ├── Addons: vpc-cni, coredns, kube-proxy, aws-ebs-csi-driver (IRSA)
    └── IRSA Roles: ebs-csi, cluster-autoscaler, aws-load-balancer-controller
```

## Cost Optimization

| Decision | Saving | Why |
|----------|--------|-----|
| **Graviton m7g/m6g** vs m7i/m6i | 20% cheaper + 15% perf/watt | ARM64, same python:3.12-slim images are wheel-clean |
| **Spot for stateless** | 70% discount | product, inventory, cart, review, notification tolerate eviction |
| **ON_DEMAND for critical** | Safety | identity, order, payment, shipping, gateway cannot be evicted mid-capture |
| **Mixed instance types** | Availability | `["m7g.medium","m6g.medium","m7g.large","m6g.large"]` — EKS diversifies spot |
| **Single NAT GW (dev)** | 66% NAT saving | `single_nat_gateway=true` for dev, `false` for prod HA |
| **Total** | **~40% vs x86 on-demand** | `capacity_type=SPOT`, `ami_type=AL2_ARM_64` |

## Files

| File | Purpose |
|------|---------|
| `versions.tf` | Terraform 1.7+, providers aws ~>5.40, kubernetes, helm, tls. Local backend (S3 example commented) |
| `variables.tf` | All tunables: graviton_instance_types, spot flags, az_count, cost tags |
| `locals.tf` | common_tags + AZ slicing + auto CIDR calc |
| `vpc.tf` | terraform-aws-modules/vpc/aws ~>5.8 — public/private subnets, NAT, ELB tags |
| `eks.tf` | terraform-aws-modules/eks/aws ~>20.17 — cluster 1.29+, addons, 2 managed NGs, IRSA roles |
| `outputs.tf` | cluster endpoint, kubeconfig command, cost summary, helm scheduling overrides |
| `main.tf` | Account data + optional namespace/ECR examples |
| `terraform.tfvars.example` | Example vars for dev |

## Node Groups Detail

### Critical (ON_DEMAND, Graviton)

- **Services:** identity (8001), order (8005), payment (8006), shipping (8007), gateway (8080)
- **Instance types:** `m7g.large`, `m6g.large`, `m7g.xlarge` (Graviton, AL2_ARM_64)
- **Capacity:** ON_DEMAND, min 1 max 6 desired 2
- **Labels:** `workload=critical`, `arch=arm64`, `tier=critical`
- **No taints** — can schedule anywhere, but critical pods have `priorityClass: ecom-critical`

### Stateless Spot (SPOT, Graviton, mixed)

- **Services:** product (8002), inventory (8003), cart (8004), review (8009), notification (8008)
- **Instance types:** `m7g.medium`, `m6g.medium`, `m7g.large`, `m6g.large` — EKS diversifies
- **Capacity:** SPOT (if `enable_spot=true`), min 1 max 10 desired 3
- **Labels:** `workload=stateless`, `arch=arm64`, `spot=true`, `tier=stateless`
- **Taint:** `spot=true:NoSchedule` — stateless pods tolerate it, critical pods don't
- **Note:** True `mixedInstancesPolicy` with weights needs Karpenter or self-managed ASG. Managed NG uses list diversification which achieves same AZ/instance spread.

## IRSA (IAM Roles for Service Accounts)

- **ebs-csi-driver:** `kube-system:ebs-csi-controller-sa` → EBS CSI policy
- **cluster-autoscaler:** `kube-system:cluster-autoscaler` → autoscaler policy
- **aws-load-balancer-controller:** `kube-system:aws-load-balancer-controller` → LBC policy

All roles use OIDC provider from EKS module.

## Usage

```bash
cd terraform/aws-graviton

# 1. Init
terraform init

# 2. Copy vars
cp terraform.tfvars.example terraform.tfvars
# Edit region, cluster_name, single_nat_gateway, etc.

# 3. Plan VPC only first (optional)
terraform plan -target=module.vpc

# 4. Apply full stack
terraform apply

# 5. Kubeconfig
aws eks update-kubeconfig --region us-east-1 --name ecom-eks-graviton
kubectl get nodes -L kubernetes.io/arch -L workload -L lifecycle
kubectl get nodes -o wide

# 6. Verify Graviton + Spot
kubectl get nodes --label-columns=beta.kubernetes.io/arch,kubernetes.io/arch,eks.amazonaws.com/capacityType
# Should show arch=arm64, capacityType=SPOT for stateless, ON_DEMAND for critical

# 7. Install ingress-nginx (Phase 4) via helm
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  -f ../../helm-charts/ingress-nginx/values.yaml

# 8. Destroy
terraform destroy
```

## Helm Scheduling Overrides

Outputs provide nodeSelector + tolerations for charts:

```yaml
# helm-charts/<svc>/values.yaml scheduling override from terraform output
scheduling:
  architecture: arm64
  nodeSelector:
    kubernetes.io/arch: arm64
  tolerations: [] # critical
  # stateless:
  tolerations:
    - key: spot
      operator: Equal
      value: "true"
      effect: NoSchedule
```

Phase 4 charts already read `scheduling.spotCapable` and `scheduling.architecture` to set these.

## Security & Best Practices

- **Private nodes:** EKS nodes in private subnets, NAT for egress
- **IRSA:** No node instance profile with broad permissions; each SA has scoped IAM role
- **Addons:** vpc-cni with prefix delegation for IP efficiency, ebs-csi with IRSA
- **Endpoint:** Public+private access; tighten `cluster_endpoint_public_access_cidrs` in prod
- **Tags:** Cost allocation tags on every resource (`Project`, `Environment`, `Cluster`, `CostOpt`)
- **Versions:** Terraform 1.7+, K8s 1.29+, AWS provider 5.40+ — pinned via `versions.tf`

## Migration Path

- **Phase 3:** Terraform provisions VPC + EKS Graviton
- **Phase 4:** Helm charts deploy 10 services with nodeSelectors for Graviton + Spot tolerations
- **Phase 5:** ArgoCD syncs from `helm-charts/` (GitOps)
- **Phase 6:** Prometheus scrapes `/metrics`, Grafana dashboards, Loki, Jaeger

## Troubleshooting

- `Error: no EC2 instances` → Check `graviton_instance_types` available in region/AZ (m7g not in all regions, fallback to m6g)
- `Spot insufficient capacity` → EKS will retry other instance types in list; add more types or set `enable_spot=false`
- `IRSA not working` → Verify OIDC provider exists: `aws iam list-open-id-connect-providers`
- `terraform plan fails on kubernetes provider` → Set `create_eks=false` first, apply VPC, then `true`

## Cost Estimate (us-east-1, dev)

- VPC + NAT (single) ~ $32/mo
- EKS control plane $73/mo
- 2x m7g.large ON_DEMAND ~ $130/mo
- 3x m7g.medium SPOT ~ $30/mo (vs $100 on-demand)
- **Total ~ $265/mo dev**, ~40% cheaper than x86 on-demand (~$440/mo)
