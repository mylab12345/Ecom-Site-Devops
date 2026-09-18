# ArgoCD Production Setup & Runbook — Phase 5

This document details the installation, configuration, bootstrapping, and day-2 operations
for ArgoCD (v2.10+) managing the 10 microservices and platform infrastructure across both
local Kind and AWS EKS Graviton clusters.

---

## 1. Architecture & The Hand-off Contract

```
┌────────────────────────────────────────────────────────┐
│               Jenkins CI Pipeline (Phase 2)            │
│  Lint ──► Tests ──► Buildx (ARM64/AMD64) ──► Trivy Gate│
└───────────────────────────┬────────────────────────────┘
                            │
               git commit to gitops/main
               (image.tag + image.digest)
                            ▼
┌────────────────────────────────────────────────────────┐
│             GitHub Repository (gitops/main)            │
│  helm-charts/<service>/values.yaml image block updated │
└───────────────────────────┬────────────────────────────┘
                            │ Webhook / 3-min poll
                            ▼
┌────────────────────────────────────────────────────────┐
│                    ArgoCD (Phase 5)                    │
│   • AppProject: ecom (RBAC + Namespace isolation)      │
│   • App-of-Apps (root-app.yaml) OR ApplicationSet      │
│   • Automated Sync + SelfHeal + Prune (foreground)     │
└───────────────────────────┬────────────────────────────┘
                            │ Reconcile desired state
                            ▼
┌────────────────────────────────────────────────────────┐
│       Kubernetes Cluster (Kind Local / AWS EKS)        │
│   Namespace: ecom (10 services, HPA, PDB, NetPol)     │
│   Namespace: ingress-nginx (Controller + Ingress)      │
└────────────────────────────────────────────────────────┘
```

### Invariant Rules
1. **CI Never Touches the Cluster**: Jenkins never executes `kubectl apply`, `helm upgrade`, or `argocd app sync`. CI's only write boundary is committing the verified image tag to `gitops/main`.
2. **Git is the Single Source of Truth**: Any manual `kubectl edit` in the cluster is rejected and restored within seconds by ArgoCD's `selfHeal: true`.
3. **Rollbacks are Commits**: Reverting a release is performed via `git revert <commit-hash>`, preserving auditability.

---

## 2. Installation

### 2.1 Install ArgoCD

ArgoCD is installed into the dedicated `argocd` namespace.

```bash
# Create namespace
kubectl create namespace argocd

# Apply official manifests (v2.10.x stable)
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.10.4/manifests/install.yaml

# Wait for all controller pods to be ready
kubectl wait --for=condition=Available deployment/argocd-server -n argocd --timeout=300s
kubectl wait --for=condition=Available deployment/argocd-repo-server -n argocd --timeout=300s
kubectl wait --for=condition=Available deployment/argocd-applicationset-controller -n argocd --timeout=300s
```

### 2.2 Retrieve Initial Admin Password

```bash
# Extract and decode initial admin password
ARGO_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)
echo "ArgoCD Admin Password: $ARGO_PASSWORD"
```

### 2.3 Access the Web UI and CLI

**Local Port Forward:**
```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
```
Open your browser at `https://localhost:8080` (bypass self-signed certificate warning) and log in with username `admin` and the password from above.

**ArgoCD CLI Login:**
```bash
argocd login localhost:8080 --username admin --password "$ARGO_PASSWORD" --insecure
```

---

## 3. Repository Authentication & Security

ArgoCD requires read access to this repository to poll `gitops/main`.

### 3.1 Configure Repository via HTTPS Token

Using a GitHub Personal Access Token (or fine-grained repository token) stored in Kubernetes secrets:

```bash
cat <<EOF | kubectl apply -n argocd -f -
apiVersion: v1
kind: Secret
metadata:
  name: repo-ecom-site-devops
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: https://github.com/mylab12345/Ecom-Site-Devops.git
  username: gitops-bot
  password: <GITHUB_TOKEN>
EOF
```

### 3.2 Configure GitHub Webhook (Instant Sync <2s)

Instead of waiting for the default 3-minute Git polling interval, configure a webhook in GitHub repository settings:
1. URL: `https://<argocd-server>/api/webhook`
2. Content type: `application/json`
3. Secret: `<shared-webhook-secret>`
4. Events: `Just the push event`

Add the webhook secret to ArgoCD:
```bash
kubectl -n argocd patch secret argocd-secret \
  -p '{"stringData": {"webhook.github.secret": "<shared-webhook-secret>"}}'
```

---

## 4. Bootstrapping the Platform

### Step 1: Create the AppProject
Isolates the microservices from cluster-admin resources and scopes deployment to authorized namespaces (`ecom`, `ingress-nginx`).

```bash
kubectl apply -f argocd/projects/ecom.yaml
```

### Step 2: Deploy Platform Applications (Choose Pattern)

#### Pattern A: App-of-Apps (Root Application) — Recommended
Deploys `argocd/root-app.yaml`, which dynamically discovers and reconciles every application in `argocd/applications/`:

```bash
kubectl apply -f argocd/root-app.yaml

# Verify root and children applications
argocd app list
kubectl get applications -n argocd
```

#### Pattern B: ApplicationSet (Matrix / List Generator)
Generates the 10 microservice applications declaratively from the service registry:

```bash
kubectl apply -f argocd/applicationset.yaml

# Monitor generated applications
kubectl get applications -n argocd -l app.kubernetes.io/part-of=ecom
```

#### Pattern C: Individual Application Deployment
Deploy platform components followed by individual services:

```bash
# Platform components
kubectl apply -f argocd/applications/ingress-nginx.yaml
kubectl apply -f argocd/applications/network-policies.yaml

# Microservices
kubectl apply -f argocd/applications/
```

---

## 5. Automated Sync, Self-Healing & Pruning

All Application manifests are preconfigured with production-grade synchronization policies:

```yaml
syncPolicy:
  automated:
    prune: true        # Deletes resources removed from Git
    selfHeal: true     # Overwrites out-of-band cluster modifications
  syncOptions:
    - CreateNamespace=true
    - ApplyOutOfSyncOnly=true
    - PrunePropagationPolicy=foreground
    - PruneLast=true
  retry:
    limit: 5
    backoff:
      duration: 5s
      factor: 2
      maxDuration: 3m
```

### Verifying Self-Healing
1. Manually mutate a deployment in the cluster:
   ```bash
   kubectl -n ecom scale deployment/order --replicas=1
   ```
2. Watch ArgoCD detect the drift and immediately restore desired state (2 replicas):
   ```bash
   kubectl get pods -n ecom -l eci.service=order -w
   ```

---

## 6. Rollback Runbook

When a deployment needs to be rolled back, follow the GitOps procedure:

### Standard Rollback: Git Revert (Audit-clean)
```bash
# 1. Fetch latest gitops branch
git fetch origin gitops/main
git checkout gitops/main

# 2. Revert the problematic bump commit
git log -n 5 --oneline
git revert <COMMIT_HASH> --no-edit

# 3. Push to gitops/main
git push origin gitops/main

# 4. ArgoCD detects the revert and triggers a zero-downtime rolling update back
argocd app wait ecom-order --sync --health
```

### Emergency Immediate Rollback: CLI Rollback
If Git access is temporarily impaired during a critical outage:
```bash
# Check deployment history
argocd app history ecom-order

# Temporarily disable self-heal to prevent immediate re-sync
argocd app set ecom-order --self-heal=false

# Roll back to previous revision ID (e.g., ID 4)
argocd app rollback ecom-order 4

# Once the incident is mitigated, align Git repository and re-enable self-heal
argocd app set ecom-order --self-heal=true
```

---

## 7. Troubleshooting Matrix

| Symptom | Probable Cause | Diagnostic Command | Remediation |
|---|---|---|---|
| `OutOfSync` | Git tag bumped, cluster not yet reconciled | `argocd app get ecom-<svc>` | Trigger manual sync: `argocd app sync ecom-<svc>` or check webhook logs |
| `ComparisonError` | Helm chart dependency or syntax error | `kubectl describe app ecom-<svc> -n argocd` | Run `helm lint helm-charts/<svc>` and `helm dependency update` |
| `Degraded` | Pod CrashLoopBackOff or probe failure | `kubectl get pods -n ecom -l eci.service=<svc>` | Inspect logs: `kubectl logs -n ecom -l eci.service=<svc> --tail=100` |
| `Unknown` repository error | Bad credentials or network unreachable | `kubectl logs -n argocd -l app.kubernetes.io/name=argocd-repo-server` | Re-check secret `repo-ecom-site-devops` token permissions |
| App stuck in `Progressing` | PDB or node resources preventing rolling update | `kubectl get pdb -n ecom` / `kubectl describe nodes` | Check PDB minAvailable and node CPU/memory pressure |
