# jenkins/ — CI setup

| File | What it is |
|---|---|
| [`setup.md`](./setup.md) | The whole guide: agent labels, plugins, JCasC, credentials, GitHub webhook, first-build runbook, timings, and the §9 failure table |
| `jcasc.yaml` (referenced in setup.md §3) | Optional controller-as-code snippet — copy it into your own file or into `configuration-as-code` |

The pipelines themselves are **one level up**, and there are two of them:

| File | Agent | Stages | Deploys? |
|---|---|---|---|
| `../Jenkinsfile.local` | `ecom-local` (docker, make, helm, kubectl, kind) | Prepare → Lint → Unit tests → Build images → Smoke test → Trivy scan → **Deploy to Kind** → **Verify deployment** | Yes — into a local Kind cluster, images from `localhost:5001` |
| `../Jenkinsfile.aws` | `ecom-buildx` (docker, buildx + QEMU, trivy) | Prepare → Lint → Unit tests → Build & push → Trivy security gate → Promote images → **GitOps bump** | No — it commits image tags to `gitops/main`; ArgoCD reconciles |

Both are short on purpose: **declarative Jenkins only** — no `script { }` block, no
fan-out — and a stage is one `sh` call into `../scripts/ci/` or the Makefile, so nothing
here has to be installed to reproduce a failure:

```bash
make ci              # lint + unit tests
make ci-plan         # resolved build matrix
make ci-all          # every stage, in order, on this machine
make helm-install-local && ./scripts/test.sh   # the local pipeline's last two stages
```

Names the controller must know:

| Kind | ID | Used for |
|---|---|---|
| Agent label | `ecom-buildx` | `Jenkinsfile.aws` — docker + buildx + trivy + hadolint + QEMU |
| Agent label | `ecom-local` | `Jenkinsfile.local` — docker + make + helm + kubectl + kind |
| Credentials | `dockerhub-creds` | registry login (push), AWS pipeline only |
| Credentials | `gitops-token` | pushing the `gitops/main` values bump, AWS pipeline only |

`Jenkinsfile.local` needs **no credentials**: the Kind registry on `localhost:5001` is
unauthenticated and the kubeconfig is whatever `terraform/local-kind` wrote.

What this directory deliberately does **not** contain: a deploy stage for anything
shared. `Jenkinsfile.aws` builds, scans and commits — `scripts/ci/lib/check_jenkinsfile.py`
fails the lint if `kubectl`, `helm upgrade` or `terraform apply` ever appears in it, and
ArgoCD (Phase 5) owns the rollout. See `setup.md` §11 for the full list of deferred
items and why.
