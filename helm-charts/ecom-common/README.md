# ecom-common (Phase 4)

Shared Helm library chart. Intended helpers:

- `common.labels` — `app.kubernetes.io/*` + the `eci: managed-by=jenkins-ci` marker, so
  `kubectl get pods -l eci.phase=2` finds exactly what this pipeline rolled out.
- probe templates keyed on the values already in `helm-charts/<service>/values.yaml`
  (`probes.readiness.path: /health`, `probes.liveness.path: /health`) — the same paths the
  Dockerfile `HEALTHCHECK` and the Phase 2 smoke test use. One health contract, three consumers.
- `PodDisruptionBudget` + `topologySpreadConstraints` from `replicaCount`.

Phase 2 deliberately does not add `Chart.yaml`/`templates/`: a chart that `helm template`
renders but nothing validates is worse than an empty directory, because it invites drift.
When this directory gains templates, add a `helm template | kubectl apply --dry-run=client`
step to `scripts/ci/lint.sh` and to the Jenkinsfile `Lint` stage in the same commit.
