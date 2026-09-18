# Jenkins CI (Phase 2) — agent, credentials, webhooks, runbook

Everything needed to turn `Jenkinsfile.local` and `Jenkinsfile.aws` into green
pipelines: what to install, what to click, what each stage runs, how to reproduce a
failure locally, and what CI deliberately does **not** do.

Target: Jenkins **LTS 2.440.3+**, Docker **24+**, buildx **0.13+**, on one Linux
amd64 agent (an arm64 agent works too — see §4). ~180 lines of this file are the
"why", the rest is copy-paste.

---

## 1. What Phase 2 delivers

| Deliverable | File | Runs |
|---|---|---|
| Pipeline-as-code, **local**: build → deploy to Kind → verify | `Jenkinsfile.local` | Jenkins, agent `ecom-local` |
| Pipeline-as-code, **cloud**: build → push → gate → promote → bump | `Jenkinsfile.aws` | Jenkins, agent `ecom-buildx` |
| Lint gate (ruff, hadolint, shellcheck, compose + structure contracts) | `scripts/ci/lint.sh` | Jenkins + `make ci-lint` |
| Unit/contract tests, two tiers, JUnit output | `scripts/ci/unit-tests.sh`, `tests/` | Jenkins + `make ci-test` |
| Multi-arch buildx build/push for all 10 services | `scripts/ci/build.sh` | Jenkins + `make ci-build` |
| Trivy CVE gate (JSON + SARIF + JUnit + decision) | `scripts/ci/scan.sh`, `.trivyignore` | Jenkins + `make ci-scan` |
| Image smoke test (`/health`, `/metrics`, `X-Request-ID`) | `scripts/ci/smoke.sh` | Jenkins + `make ci-smoke` |
| GitOps trigger: bump `helm-charts/<svc>/values.yaml` image tags | `scripts/ci/gitops-bump.sh` | Jenkins + `make ci-gitops` |
| One-command local run of the whole pipeline | `scripts/ci/all.sh` | `make ci` |
| Service catalog the pipeline fans out over | `scripts/ci/lib/services.sh` | all of the above |

**The cloud pipeline deploys nothing.** Its last step is a commit that changes two
YAML lines per service (`image.repository`, `image.tag`); ArgoCD (Phase 5) reconciles
them. `Jenkinsfile.local` is the deliberate exception: it installs the charts into a
Kind cluster on the same machine, which is the fastest way to see a change running —
and a test fails the build if `kubectl`/`helm upgrade` ever appears in the AWS one.

### Stage flow

```
Jenkinsfile.aws    (agent ecom-buildx)
  Prepare ─► Lint ─► Unit tests ─► Build & push ─► Trivy security gate ─► Promote images ─► GitOps bump
    │          │          │             │                  │                   │                │
  doctor +   ruff etc  pytest in    load → smoke →     scan the pushed    imagetools        commit tags to
  buildx +   compose   python:3.12  push amd64+arm64   refs (both archs)  create → latest   gitops/main
  QEMU,login parity    both tiers   tag = git SHA                                           (trunk only)

Jenkinsfile.local  (agent ecom-local)
  Prepare ─► Lint ─► Unit tests ─► Build images ─► Smoke test ─► Trivy scan ─► Deploy to Kind ─► Verify deployment
    │                                        │                                    │                  │
  make doctor,                    push to localhost:5001                 make helm-install-local   rollout status +
  find kubeconfig                                                                    (IMAGE_TAG)     scripts/test.sh
```

Both files follow two rules: **declarative only** (no `script { }` block anywhere —
`check_jenkinsfile.py` fails the lint if one appears) and **a stage is a single `sh` call
into `scripts/ci/` or the Makefile.** Values live in the `environment {}` block and every
`sh` is single-quoted, so Groovy interpolates nothing into a shell command. There is no
per-service fan-out — `build.sh` bounds its own concurrency (`ECOM_BUILD_PARALLEL`) and
writes one `.ci-output/images.txt`.

`Jenkinsfile.aws` stages worth knowing before your first failure:

- **`Prepare`** — runs `make doctor`, creates the buildx builder, registers QEMU, and
  logs in to the registry with `--password-stdin` into a per-build `DOCKER_CONFIG`. No
  tag is computed in Groovy: `build.sh` derives the candidate tag from the git SHA
  (`-dirty` if the tree is not clean), and `scan.sh`/`--promote` derive the same value,
  so a rebuild of one commit bumps nothing.
- **`Build & push`** — `--load` (host arch) → `smoke.sh` → `--push` (amd64+arm64,
  `--cache`, `retry(2)`). The push reuses the layers the load already built, so it
  costs an upload, not a rebuild.
- **`Trivy security gate`** — one `scan.sh` call over the **pushed** candidates (the
  bytes the cluster will pull, both architectures).
- **`Promote images`** — `docker buildx imagetools create` retags candidate →
  `latest` **inside the registry**. No rebuild ⇒ the promoted image is byte-identical
  to the one that passed the gate.
- **`GitOps bump`** — trunk only (`env.BRANCH_NAME == 'main'`). Rewrites
  `image.repository`/`image.tag` and pushes `gitops/main`. ArgoCD syncs. Rollback =
  `git revert` + ArgoCD.

`Jenkinsfile.local` adds the two stages the cloud pipeline is not allowed to have:

- **`Deploy to Kind`** — `make helm-install-local IMAGE_TAG=<this build>`, with
  `KUBECONFIG` from `make -s kind-kubeconfig` (the path terraform wrote, else
  `~/.kube/config`); images come from `localhost:5001`, the registry
  `terraform/local-kind` runs. No credentials.
- **`Verify deployment`** — `kubectl -n ecom wait --for=condition=Available deployment
  --all`, then `BASE=http://localhost:8080 ./scripts/test.sh` through the ingress.
  `DEPLOY=false` stops the pipeline after the scan.

---

## 2. Prerequisites on the agent

```bash
# Docker Engine 24+ (rootless is fine; the job only needs the socket)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker jenkins            # restart the agent afterwards

# buildx (bundled with Docker ≥23; otherwise)
mkdir -p ~/.docker/cli-plugins
curl -fsSL -o ~/.docker/cli-plugins/docker-buildx \
  https://github.com/docker/buildx/releases/download/v0.14.1/buildx-v0.14.1.linux-amd64
chmod +x ~/.docker/cli-plugins/docker-buildx
docker buildx version

# Trivy (security gate) + hadolint/shellcheck (lint stage)
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt-get update && sudo apt-get install -y trivy
sudo curl -L -o /usr/local/bin/hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64 && sudo chmod +x /usr/local/bin/hadolint
sudo apt-get install -y shellcheck jq git python3-pip

# Verify everything the pipeline expects, in one line:
bash scripts/ci/all.sh --doctor
```

`--doctor` prints the same table the `Prepare` stage prints, and it is the first
thing to run when a build fails with `STRICT_TOOLS` complaints.

**Optional tools and what you lose without them:** `hadolint` → the Dockerfile lint
advisory is skipped; `shellcheck` → shell findings unreported; `trivy` → **the build
fails** (a security gate that silently disappears is worse than no gate; that is
`STRICT_TOOLS=1`, the default).

## 3. Jenkins controller

Install the LTS WAR/package or run the container (pick one; the container needs the
extra mounts below):

```yaml
# /opt/jenkins/docker-compose.yaml — controller only, builds run on the agent
services:
  jenkins:
    image: jenkins/jenkins:2.440.3-lts-jdk17
    restart: unless-stopped
    ports: ["8080:8080", "50000:50000", "8081:8081"]   # 8081 = GitHub webhook receiver
    environment:
      JAVA_OPTS: "-Dhudson.model.DownloadService.noSignatureCheck=true -Xmx2g"
    volumes:
      - jenkins_home:/var/jenkins_home
volumes:
  jenkins_home:
```

### Plugins (Manage Jenkins → Plugins, or the JCasC block below)

| Plugin | Why |
|---|---|
| Git, GitHub, GitHub Branch Source | SCM + PR discovery |
| Pipeline (Blue Ocean optional) | declarative `Jenkinsfile` |
| Credentials Binding | `withCredentials` used by the pipeline |
| Docker Commons / Docker Pipeline | `docker login`/`logout` step types |
| JUnit | publishes pytest + Trivy results |
| Workspace Cleanup | `cleanWs()` in `post` |
| Timestamper, Ansicolor, Build Timeout | stage logs are readable |
| Config as Code (JCasC) | the controller config itself is git-tracked |

```yaml
# jenkins/jcasc.yaml — declarative controller config (plugins + agent label + creds id)
jenkins:
  systemMessage: "ECom CI — Phase 2 (build/test/scan/gitops-bump; no deploys)"
  numExecutors: 0                       # controller builds nothing
  labelString: controller
  securityRealm: local
  authorizationStrategy: loggedInUsersCanDoAnything
  clouds:
    - docker:
        dockerDir: "/var/run/docker.sock"
        connectTimeout: 10
        readTimeout: 15
unclassified:
  location:
    url: "http://jenkins.internal:8080/"
tool:
  git:
    installations:
      - name: Default
        home: git
credentials:
  system:
    globalCredentialsProvider:
      jenkins-creds:
        usernamePassword:
          - id: dockerhub-creds
            description: Docker Hub push (CI scoped token)
            username: mylab12345
            password: "${DOCKERHUB_CI_TOKEN}"      # from the environment, never a literal
          - id: gitops-token
            description: GitHub PAT — contents:read+write on this repo only
            username: ecom-ci-bot
            password: "${GITHUB_CI_TOKEN}"
```

> `labelString` on the agent (not the controller) is what `agent { label
> 'ecom-buildx' }` matches. Keep the controller at `numExecutors: 0`: a build agent
> is a code-execution primitive, and putting the controller on it turns any pipeline
> bug into a controller-root shell.

### Agent node

Two labels, one per pipeline: `ecom-buildx` (docker, buildx, QEMU, trivy) for
`Jenkinsfile.aws`, and `ecom-local` (docker, make, python3, helm, kubectl, kind) for
`Jenkinsfile.local`. They can be the same box — give it both labels — but only the
local one needs a Kind cluster and a kubeconfig.

Manage Jenkins → Nodes → New Node → **Permanent agent**, labels `ecom-buildx ecom-local
linux docker amd64 arm-capable`, launch method "Attach JNLP agent" (or inbound SSH):

```bash
# On the agent box, once:
java -jar agent.jar -url http://jenkins.internal:8080/ -secret <secret> \
     -name ecom-buildx-01 -workDir /var/lib/jenkins-agent -labels "ecom-buildx linux docker amd64"
```

Requirements: `docker` socket access, ≥8 GB RAM and ≥4 free cores (10 images × 2
platforms), 30 GB for `/var/lib/docker` + the builder cache, and **no other
workload** on that host (QEMU emulation is CPU-hungry; see §4).

## 4. Multi-arch: the part that bites

`linux/arm64` on an amd64 agent runs under QEMU, which needs one privileged
container the first time (the `Prepare` stage registers it automatically):

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx create --name ecom-buildx --driver docker-container --bootstrap --use
docker buildx inspect ecom-buildx | grep -i platforms     # expect linux/amd64, linux/arm64, …
docker run --rm --platform linux/arm64 alpine uname -m   # must print aarch64
```

Notes that save hours:

- The **default** `docker` driver cannot push a manifest list. `docker-container` can,
  and it keeps a layer cache between builds (that cache lives in a volume, not the
  workspace — `CLEAN_WS` does not destroy it).
- `--load` and multi-platform are mutually exclusive: buildx refuses to load a
  manifest list into the docker store. `build.sh` narrows `--load` to the host arch
  for exactly this reason, which is also why the smoke test is amd64-only and the
  arm64 leg is validated by the manifest inspect in `build.sh`.
- `exec format error` ⇒ QEMU/binfmt missing or the wrong manifest. Re-run the
  `binfmt` command; if you build on an **arm64** agent, nothing to do (arm64 is
  native, amd64 emulated — same cost, mirrored).
- Our images are `python:3.12-slim` + `psycopg2-binary`/`bcrypt` wheels: no
  architecture-specific source builds, so the QEMU tax is apt/pip I/O, not
  compilation. Rule of thumb: multi-arch here costs ~2× the wall clock of
  single-arch, not 10×. If a build ever needs gcc for an sdist on arm64, add
  `--set-platform` caching (`--cache`) and prefer an arm64 agent for that service.
- Emulation is slow enough that the 75-minute pipeline timeout in `Jenkinsfile.aws` is
  deliberate (`Jenkinsfile.local` builds one platform and gets 40 minutes). Cold (no builder cache, 10 services × 2 platforms): **25–40 min**.
  Warm cache: **8–14 min**. If you see 40 min repeatedly, check `docker buildx du`
  for a cache that is not being used (`--cache` is push-mode only).

## 5. Security gate (Trivy)

Policy, in the order it is applied:

1. `--severity HIGH,CRITICAL` (`TRIVY_SEVERITY` parameter).
2. `--ignore-unfixed` — a finding with no released fix is a *ticket*, not a broken
   build. Without this, base-image CVEs freeze every deploy permanently.
3. `.trivyignore` at the repo root — only for fixable findings we consciously
   accept. Entries must be advisory IDs (`CVE-…`/`GHSA-…`) and `tests/test_ci_hygiene.py`
   caps the list at 40 and rejects globs, so the allowlist cannot quietly become the
   real policy.
4. `SCAN_GATE=true` ⇒ `--mode gate`, findings fail the build. `false` ⇒
   `--mode report`, the stage exits 0 and the findings are in the artifacts (use
   during the first week, then turn it back on). It is `false` by default in
   `Jenkinsfile.local` and `true` in `Jenkinsfile.aws`.

The DB download (~60 MB) is paid once per build: there is a single `scan.sh`
invocation, no fan-out to race. Air-gapped agents: mount a cached
`/root/.cache/trivy-db` and pass `--skip-db-update`.

`Jenkinsfile.local` scans refs in `localhost:5001`, which is plain HTTP, so it sets
`TRIVY_INSECURE=1`. Without it trivy fails the pull with an x509 error and the stage
reports a *scan error* instead of a verdict. Leave it off for docker.io/ECR.

Reports: `.ci-output/trivy/<ref>.{json,sarif,txt}` plus `.ci-output/junit-trivy.xml`,
which both pipelines publish with `junit`.
SARIF feeds GitHub code scanning / DefectDojo later (Phase 6); JUnit is what turns
the build yellow/red in the UI.

## 6. Credentials & least privilege

| ID | Type | Scope | Used by |
|---|---|---|---|
| `dockerhub-creds` | Username/password | Docker Hub **patron/read-write on `ecom-*` only**, never the account password | `Prepare` login, `Build & push` |
| `gitops-token` | Username/password | GitHub **fine-grained PAT**: `contents:write` on this repo, nothing else | `GitOps bump` |

`Jenkinsfile.local` uses **no credentials at all**: `localhost:5001` is an
unauthenticated registry on the same host, and the kubeconfig it deploys with is
whatever `terraform/local-kind` wrote.

Rules that keep the blast radius small:

- Registry login writes to `DOCKER_CONFIG="${WORKSPACE}/.docker"` — a per-build
  config, so two jobs on one agent can never read each other's auth, and the
  credential is gone when the workspace is.
- `post { always }` runs `docker logout` — the `auths` block is exactly the kind of
  file that leaks through `archiveArtifacts` or a copied agent.
- `gitops-token` cannot merge a PR and cannot touch a deployment: it can push a
  branch. If it is compromised, the worst case is a values bump to a non-protected
  branch, which ArgoCD won't apply unless it is merged (Phase 5 pins
  `argocd`'s repo credentials separately).
- Rotate both tokens quarterly; a Jenkins credential is not a vault — anything with
  runtime secret material (SMTP, payment keys) belongs to SOPS/Vault in Phase 4/6,
  referenced from `values.yaml` by **Secret name only** (see the `config:` blocks in
  `helm-charts/*/values.yaml`).

## 7. GitHub webhook

Push events, not polling (polling is 5 min of latency by default).

1. Jenkins must be reachable from GitHub: `http(s)://jenkins.internal:8080/github-webhook/`.
   Behind an ingress, expose 8080 (or run the receiver on 8081 as above).
2. Manage Jenkins → Security → **GitHub** section: add the API endpoint and the
   "GitHub token for status updates" credential (`github-api-token`, scope
   `repo:status` or `repo_deployment`) — that is what turns the yellow dot next to a
   commit in GitHub into green/red.
3. GitHub → repo → Settings → Webhooks → Add webhook:
   - Payload URL `…/github-webhook/`, Content type `application/json`
   - Events: **Custom** → `Push`, `Pull request`
   - Secret: set one, then in Jenkins install *GitHub Branch Source* → the folder
     picks it up ("hook secret" on the GitHub App / Org source).
4. Multibranch Pipeline: **Scan Repository Triggers** off, Webhook on. Add
   `Periodically if not otherwise run: 1 day` as a safety net for missed hooks.
5. `[skip ci]` in a commit message: the bump commits carry it, so the ArgoCD-facing
   commit does not re-trigger this pipeline (infinite-loop guard).

Verify: `curl -s -X POST http://jenkins.internal:8080/github-webhook/ -d '{}'`
should return `200`/`400` — a `404` means the URL/path is wrong, a refused
connection means ingress.

## 8. First build — runbook

```bash
# 1. Everything the pipeline needs, locally, before touching Jenkins
cp .env.example .env
make ci                 # lint + tests (+ structure contracts)
make ci-build           # buildx --load, host arch, then smoke
docker run --rm -it --entrypoint sh ecom-product:<tag> -c 'curl -sf localhost:8002/health'

# 2. Registry login on the agent (only for the very first manual push test)
echo "$HUB_TOKEN" | docker login -u mylab12345 --password-stdin
bash scripts/ci/build.sh --push --registry docker.io/mylab12345 --services product --cache

# 3. Jenkins: create two Pipeline jobs from SCM —
#      • Script Path `Jenkinsfile.aws`,   agent label ecom-buildx
#      • Script Path `Jenkinsfile.local`, agent label ecom-local
#    First AWS run with SCAN_GATE=false + GITOPS_ENABLED=false (validates agent, lint,
#    tests, build, push), then PROMOTE_LATEST=true, then GITOPS_ENABLED=true.
```

Staged rollout of the gates matters: turning on push + gate + bump on the first run
means you cannot tell a Dockerfile problem from a registry-auth problem from a
values-file problem. Three runs, each isolating one class of failure.

| What you should see | Where |
|---|---|
| one build line per service, concurrency bounded by `ECOM_BUILD_PARALLEL` | `Build & push` stage log |
| `build-<svc>.log` per service | Build Artifacts (`.ci-output/**`) |
| `manifest lists: linux/amd64/linux/arm64, …` | build log tail per service (`build.sh` verifies it) |
| `junit-structure.xml`, `junit-app.xml`, `junit-trivy.xml` | Test Result tab |
| `images.txt`, `summary.md`, `trivy-*/`, `build-*.log` | Build Artifacts (`.ci-output/**`) |
| a commit on `gitops/main` touching 10 `values.yaml` | GitHub → branches |

## 9. Failure → cause → fix

Both bump-recovery rows below were rehearsed against a second writer (two clones
pushing into one bare remote): disjoint bumps replay cleanly and neither tag is
lost; a conflicting one aborts the rebase, leaves the remote untouched and restores
the agent workspace to the SHA it started from.

| Symptom | Cause | Fix |
|---|---|---|
| `unknown service(s): x` | typo in `SERVICES` | names come from `scripts/ci/lib/services.sh`; `build.sh --list-services` prints them |
| `docker: 'buildx' is not a docker command` | buildx plugin not installed **for the jenkins user** | §2 (`~jenkins/.docker/cli-plugins`) — it is per-user |
| `failed to solve: exec: "qemu-aarch64": executable file not found` | binfmt not registered / agent not privileged | §4 first command; on Kubernetes agents, `securityContext.privileged: true` |
| `push rejected … denied: requested access to the resource is protected` | credential is read-only or the hub org is wrong | check `dockerhub-creds`, and `REGISTRY` must be the **org**, not a repo |
| `no matching manifest for linux/arm64/v8` (on `docker run`) | you ran the amd64 image on arm | `--platform linux/amd64` or rebuild; our manifest list covers both |
| `trivy: database lookup failure` / `toomanyrequests` | ghcr.io rate limit | `--download-db-only` in a nightly job; mount a cached DB volume |
| `SECURITY GATE FAILED — … CRITICAL=1` | real finding with a fix | bump the pinned dep / the base image; only then `.trivyignore` with an owner |
| `no image manifest produced` / bump says "nothing to do" | `build.sh` never reached the push, or `SERVICES` matched nothing | `bash scripts/ci/build.sh --list-services --services "$SERVICES"` prints what it resolved |
| `helm-charts/<svc>/values.yaml has an image: block this tool will not rewrite` | someone hand-edited the stub into an unexpected shape | restore the 3-line `image:` mapping (see `helm-charts/product/values.yaml`) — refusing to write is intentional |
| bump pushes nothing, "already current" | tag unchanged (rebuild of the same commit) | expected; it is idempotent by design |
| `push rejected — replaying the bump on top of origin/gitops/main` and the build still goes green | a human or another branch's build moved the branch mid-run | no action: the rewrite is deterministic, so `gitops-bump.sh` replayed it. Confirm both bumps landed: `git log --oneline origin/gitops/main \| head` |
| `rebase onto origin/gitops/main failed — most likely two bumps touched the same image.tag` | two builds disagree about which SHA a service should run | the loser's own commit is still valid — re-run `make gitops-bump PUSH=1` with that build's `.ci-output/images.txt`, or hand-edit. Auto-resolving is deliberately impossible: whichever tag you pick, a deployment silently disagrees with a green build |
| `ERROR: The project … cannot be built` (GitHub) | hook not firing | §7 verify `curl -X POST …/github-webhook/` |
| AWS build is green but nothing deployed | correct by design | `Jenkinsfile.aws` only commits tags — ArgoCD rolls out. To see it in a cluster now, run `Jenkinsfile.local` |
| local build green, pods `ImagePullBackOff` | Kind cannot reach the registry | `kubectl -n ecom describe pod` — the node needs the `localhost:5001` mirror config from `terraform/local-kind` |

## 10. Parameters worth knowing

Deliberately few — a parameter is a knob somebody has to understand at 3 a.m.
`STRICT_TOOLS` and the registry/tag knobs still exist as environment variables
(`.env.example`), they are just not per-build choices any more.

| Parameter | Pipeline | Default | Notes |
|---|---|---|---|
| `SERVICES` | both | `all` | `all`, or a comma list; `auto` (only changed services) also works — `build.sh` resolves it |
| `SCAN_GATE` | both | `false` local / `true` aws | `false` = `scan.sh --mode report`: findings in the artifacts, build stays green |
| `IMAGE_TAG` | local | *(empty)* | empty ⇒ `local-<build number>`. The AWS pipeline has no tag parameter: `build.sh` derives the git SHA |
| `DEPLOY` | local | `true` | `false` ⇒ stop after the scan; leave the Kind cluster alone |
| `REGISTRY` | aws | `docker.io/mylab12345` | the **org/namespace**, not a repo |
| `PLATFORMS` | aws | `linux/amd64,linux/arm64` | drop arm64 for a fast amd64-only path on a feature branch |
| `PROMOTE_LATEST` | aws | `true` | `false` ⇒ candidate tag only, `latest` untouched |
| `GITOPS_ENABLED` | aws | `true` | also gated on `BRANCH_NAME == main` |
| `GITOPS_BRANCH` | aws | `gitops/main` | must not equal the base branch — `gitops-bump.sh` refuses |

## 11. Deliberately not in Phase 2

- **Deploying to anything shared.** No `kubectl`/`helm upgrade`/`argocd sync` in
  `Jenkinsfile.aws`, and `check_jenkinsfile.py` fails the lint if one appears. Phase 4
  ships the charts, Phase 5 wires ArgoCD; the bump stage is the seam between them.
  `Jenkinsfile.local` deploys to a Kind cluster on the build agent, which is a laptop,
  not a blast radius.
- **Chart publishing.** `helm-charts/*/values.yaml` are contract stubs now; the
  chart bodies arrive in Phase 4 (`Jenkinsfile.aws` bump works either way — it creates
  the `image:` block if a chart directory is new).
- **Image signing / SBOM attestation** (`cosign`, `--provenance`, `--sbom`): Phase 6,
  with the OCI registry layout and the policy controller. `ECOM_PROVENANCE=1`
  already threads through `build.sh` so enabling it is a config change, not a rewrite.
- **Nightly full-fleet rescans**: Phase 6 (a CronJob in-cluster plus a
  `Jenkinsfile.nightly` that reruns `scan.sh` against the moving `latest` tag).
- **Distributed QEMU/remote buildkitd** for faster arm64: worth it only if the
  multi-arch stage exceeds ~20 min warm; documented for Phase 3.

## 12. Checklist after setup

- [ ] `bash scripts/ci/all.sh --doctor` → no `MISSING` lines except `yq`
- [ ] `make ci` green locally
- [ ] Jenkins `Prepare` runs `make doctor` clean and creates the `ecom-buildx` builder
- [ ] `docker run --rm --platform linux/arm64 alpine uname -m` → `aarch64`
- [ ] First `Jenkinsfile.aws` build with `GITOPS_ENABLED=false` → images pushed, no bump commit
- [ ] Second build with `PROMOTE_LATEST=false` → candidate tag only, `latest` untouched
- [ ] Third build with everything on → 10 images in the registry, one commit on `gitops/main`
- [ ] One `Jenkinsfile.local` build → 10 images in `localhost:5001`, pods `Running` in Kind,
      `scripts/test.sh` green through the ingress
- [ ] `gitops/main` commit diff touches **only** `image.repository`/`image.tag`
- [ ] GitHub shows a green check on the commit that triggered the build
- [ ] Re-running the same commit produces **no** new bump commit (idempotency)
