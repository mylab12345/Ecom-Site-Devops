# jenkins/ — Phase 2 CI

| File | What it is |
|---|---|
| [`setup.md`](./setup.md) | The whole Phase 2 guide: agent labels, plugins, JCasC, credentials, GitHub webhook, first-build runbook, timings, and the §9 failure table |
| `jcasc.yaml` (referenced in setup.md §3) | Optional controller-as-code snippet — copy it into your own file or into `configuration-as-code` |

The pipeline itself is **one level up**: `../Jenkinsfile`. Its logic is in
`../scripts/ci/`, so nothing here needs to be installed to reproduce a failure:

```bash
make ci              # lint + unit tests
make ci-plan         # resolved build matrix
make ci-build        # buildx --load + smoke, locally
bash scripts/ci/all.sh --keep-going   # every stage, in order
```

Two names the controller must know, both referenced from `../Jenkinsfile`:

| Kind | ID | Used for |
|---|---|---|
| Agent label | `ecom-buildx` | Docker + buildx + trivy + hadolint + QEMU |
| Credentials | `dockerhub-creds` | registry login (push) |
| Credentials | `gitops-token` | pushing the `gitops/main` values bump |

What this directory deliberately does **not** contain: a deploy stage, `kubectl`,
`helm upgrade`, or `argocd sync`. CI builds, scans and commits; ArgoCD (Phase 5)
deploys. See `setup.md` §11 for the full list of deferred items and why.
