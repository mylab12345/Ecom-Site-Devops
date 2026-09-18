# argocd/ — Phase 5: GitOps Delivery ✅ COMPLETE

Declarative GitOps engine managing continuous delivery for all 10 e-commerce microservices,
platform networking, and ingress controllers across both local Kind and AWS EKS Graviton.

---

## 1. Directory Structure

```
argocd/
├── README.md                      # Architecture overview and usage (this file)
├── setup.md                       # Complete production installation and runbook
├── root-app.yaml                  # Root App-of-Apps application CR
├── applicationset.yaml            # Generator for all 10 microservices from registry
├── projects/
│   └── ecom.yaml                  # AppProject CR (RBAC, destination and repo constraints)
└── applications/
    ├── identity.yaml              # Port 8001 (critical)
    ├── product.yaml               # Port 8002 (stateless)
    ├── inventory.yaml             # Port 8003 (stateless)
    ├── cart.yaml                  # Port 8004 (stateless)
    ├── order.yaml                 # Port 8005 (critical)
    ├── payment.yaml               # Port 8006 (critical)
    ├── shipping.yaml              # Port 8007 (critical)
    ├── notification.yaml          # Port 8008 (stateless)
    ├── review.yaml                # Port 8009 (stateless)
    ├── gateway.yaml               # Port 8080 (edge)
    ├── ingress-nginx.yaml         # Multi-arch ingress controller wrapper
    └── network-policies.yaml      # Zero-trust network isolation policies
```

---

## 2. GitOps Workflow & Architecture

```
                                      Phase 2 Jenkins CI
                                               │
                                  Pushes multi-arch images &
                                commits to gitops/main branch
                                               │
                                               ▼
                              ┌──────────────────────────────────┐
                              │ Git Repository (gitops/main)     │
                              │ • helm-charts/<svc>/values.yaml  │
                              └────────────────┬─────────────────┘
                                               │
                                      ArgoCD Reconciles
                                               │
                   ┌───────────────────────────┴───────────────────────────┐
                   ▼                                                       ▼
      ┌─────────────────────────┐                             ┌─────────────────────────┐
      │   argocd/root-app.yaml   │                             │ argocd/applicationset   │
      │   (App-of-Apps Pattern) │                             │   (List Generator)      │
      └────────────┬────────────┘                             └────────────┬────────────┘
                   │                                                       │
                   └───────────────────────────┬───────────────────────────┘
                                               ▼
                              ┌──────────────────────────────────┐
                              │      argocd/projects/ecom.yaml   │
                              │ (Scoped to ecom & ingress-nginx) │
                              └────────────────┬─────────────────┘
                                               │
        ┌──────────────┬──────────────┬────────┼──────────────┬──────────────┐
        ▼              ▼              ▼        ▼              ▼              ▼
     ecom-cart    ecom-order    ecom-gateway  ...      ingress-nginx  network-policies
```

### The Invariant Contract
- **No cluster write permissions for CI**: Jenkins creates verified artifacts and records the state in Git (`gitops/main`).
- **Automated Reconcile**: ArgoCD watches `gitops/main`. When values change, ArgoCD executes a rolling update.
- **Drift Protection (Self-Healing)**: Manual changes inside the cluster are detected and immediately overwritten by ArgoCD.
- **Pruning**: Removed resources from Helm charts are cleanly pruned in foreground mode.

---

## 3. Deployment Patterns

### Pattern A: App-of-Apps (`root-app.yaml`) — Recommended
Synchronizes all manifests in `argocd/applications/`:
```bash
kubectl apply -f argocd/projects/ecom.yaml
kubectl apply -f argocd/root-app.yaml
```

### Pattern B: ApplicationSet (`applicationset.yaml`)
Dynamically creates 10 Application CRs from the central service catalog:
```bash
kubectl apply -f argocd/projects/ecom.yaml
kubectl apply -f argocd/applicationset.yaml
```

### Pattern C: Direct Service Applications
Deploy individual services or platform charts:
```bash
kubectl apply -f argocd/projects/ecom.yaml
kubectl apply -f argocd/applications/gateway.yaml
kubectl apply -f argocd/applications/product.yaml
```

---

## 4. Verification & Status Commands

```bash
# List all managed applications
argocd app list

# Inspect synchronization and health of a specific service
argocd app get ecom-order

# View diff between Git and cluster
argocd app diff ecom-payment

# Tail live rollout events
argocd app logs ecom-gateway

# Trigger manual synchronization
argocd app sync ecom-product --prune
```

See `setup.md` for full installation, initial password extraction, and rollback procedures.
