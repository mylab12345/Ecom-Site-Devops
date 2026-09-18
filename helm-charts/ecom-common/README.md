# ecom-common (Phase 4) — Shared Helm Library Chart

Library chart providing common templates for all 10 microservices. Ensures one health contract, one label contract, one scheduling contract.

## Usage

Add as dependency in `helm-charts/<service>/Chart.yaml`:

```yaml
dependencies:
  - name: ecom-common
    version: 0.1.0
    repository: file://../ecom-common
```

Then in templates:

```yaml
labels:
  {{- include "ecom-common.labels" . | nindent 4 }}
```

## Helpers

| Helper | Description |
|--------|-------------|
| `ecom-common.fullname` | Release + Chart name truncated to 63 chars |
| `ecom-common.labels` | `app.kubernetes.io/*` + `eci.managed-by=jenkins-ci` + `eci.phase=4` + arch |
| `ecom-common.selectorLabels` | Selector labels for Deployment/Service |
| `ecom-common.serviceAccountName` | SA name |
| `ecom-common.image` | `repository:tag` |
| `ecom-common.probes.readiness` | HTTP probe from `values.probes.readiness` (path `/health`) — same path Dockerfile HEALTHCHECK and Phase 2 smoke test use |
| `ecom-common.probes.liveness` | Liveness probe |
| `ecom-common.resources` | Requests/limits with Graviton sane defaults |
| `ecom-common.nodeSelector` | `kubernetes.io/arch: arm64` when `scheduling.architecture=arm64` |
| `ecom-common.tolerations` | Adds `spot=true:NoSchedule` when `scheduling.spotCapable=true` |
| `ecom-common.topologySpreadConstraints` | Spread across AZs + hostname |
| `ecom-common.securityContext` | non-root, fsGroup 1000, seccomp RuntimeDefault |
| `ecom-common.containerSecurityContext` | drop ALL, no priv escalation |
| `ecom-common.env` | Renders `values.config` as env vars |
| `ecom-common.pdb` | PodDisruptionBudget template |
| `ecom-common.priorityClassName` | From `scheduling.priorityClass` |

## Contracts Enforced

- **Health:** Probes use `probes.readiness.path: /health` — same as Dockerfile `HEALTHCHECK` and Phase 2 smoke test. One contract, three consumers.
- **Labels:** `eci.managed-by=jenkins-ci` marker so `kubectl get pods -l eci.phase=4` finds exactly what this pipeline rolled out.
- **Scheduling:** `scheduling.architecture` drives nodeSelector, `scheduling.spotCapable` drives tolerations for spot pools from terraform/aws-graviton outputs.
- **PDB + Spread:** From `replicaCount` — critical services have `ecom-critical` priorityClass, stateless have `ecom-best-effort`.

## Lint

```bash
helm lint helm-charts/product
helm template ecom helm-charts/product | kubectl apply --dry-run=client -f -
```

Phase 2 deliberately did not add `Chart.yaml`/`templates/` until now: a chart that `helm template` renders but nothing validates is worse than an empty directory. Now that this directory has templates, add a `helm template | kubectl apply --dry-run=client` step to `scripts/ci/lint.sh` — which is what the `Lint` stage of both `Jenkinsfile.local` and `Jenkinsfile.aws` runs — in the same commit (Phase 4).
