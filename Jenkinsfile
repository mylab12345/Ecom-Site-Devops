// ─────────────────────────────────────────────────────────────────────────────
//  E-Commerce Microservices Platform — Jenkins CI (Phase 2)
//  Lint → unit tests → buildx multi-arch build → smoke → push → Trivy gate →
//  promote → GitOps tag bump (ArgoCD deploys; this pipeline never does)
//
//  Design rules, ordered by how much they cost to violate:
//   1. Every stage shells out to ./scripts/ci/*.sh. There is not one docker,
//      trivy or git one-liner of consequence in here, so `make ci` on a laptop
//      and a Jenkins run execute identical logic and cannot disagree.
//   2. The last act of a green build is a *commit* that changes image tags in
//      helm-charts/*/values.yaml. Rollback is `git revert`, not "replay build 7".
//   3. Images are pushed under an immutable candidate tag, scanned in the
//      registry, and only then promoted. A `latest` that skipped the gate cannot
//      exist, because nothing writes `latest` except the promote stage.
//   4. Fan-out is per service and capped, so a broken Dockerfile fails its own
//      branch instead of serialising (or melting) the fleet.
//   5. The service list is never restated here — it is read from
//      scripts/ci/lib/services.sh (tests/test_ci_hygiene.py enforces that).
//
//  Job type: Multibranch Pipeline (or a Pipeline job pointed at this file).
//  Required plugins / credentials / agent labels: see jenkins/setup.md
//  Local dry-run of the same stages: `bash scripts/ci/all.sh --keep-going`
// ─────────────────────────────────────────────────────────────────────────────

pipeline {
  // Linux agent with docker + buildx + trivy + hadolint + QEMU (setup.md §3–§5).
  agent { label 'ecom-buildx' }

  options {
    timestamps()
    disableConcurrentBuilds()                 // buildx cache + binfmt registration do not share well
    buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
    timeout(time: 75, unit: 'MINUTES')
    skipDefaultCheckout(true)                 // Prepare does the checkout, on the right ref
    quietPeriod(5)                            // coalesce push bursts during a rebase
  }

  parameters {
    string(name: 'REGISTRY', defaultValue: '', description: 'Destination registry namespace, e.g. docker.io/mylab12345 or localhost:5000/ecom. Empty + PUSH_IMAGES=false → build and scan only.')
    string(name: 'PLATFORMS', defaultValue: 'linux/amd64,linux/arm64', description: 'buildx platforms. Keep arm64: Phase 3 schedules on Graviton.')
    string(name: 'SERVICES', defaultValue: 'auto', description: 'all | auto (only changed services) | comma list. The catalog itself lives in scripts/ci/lib/services.sh.')
    string(name: 'IMAGE_TAG', defaultValue: '', description: 'Override the candidate tag. Empty → <branch>-<short-sha>.')
    string(name: 'MAX_PARALLEL_BUILDS', defaultValue: '4', description: 'Concurrent service builds on this agent. Ten multi-arch builds at once will starve a 16-core box.')
    string(name: 'TRIVY_SEVERITY', defaultValue: 'HIGH,CRITICAL', description: 'Severities the gate blocks on.')
    string(name: 'GITOPS_BRANCH', defaultValue: 'gitops/main', description: 'Branch the values bump is pushed to. Deliberately not the base branch.')
    booleanParam(name: 'PUSH_IMAGES', defaultValue: true, description: 'Push the candidate tag. Off = build + smoke + scan only (agents without registry credentials).')
    booleanParam(name: 'RUN_SMOKE', defaultValue: true, description: 'docker run + GET /health, /metrics per image (scripts/ci/smoke.sh).')
    booleanParam(name: 'STRICT_TOOLS', defaultValue: true, description: 'Fail when trivy/hadolint are missing instead of quietly skipping a gate.')
    booleanParam(name: 'SCAN_GATE', defaultValue: true, description: 'true: CRITICAL/HIGH with an available fix fails the build. false: report only (build goes UNSTABLE).')
    booleanParam(name: 'GITOPS_ENABLED', defaultValue: true, description: 'Commit + push the helm values tag bump. Trunk branches only.')
    booleanParam(name: 'PROMOTE_LATEST', defaultValue: true, description: 'After the gate, retag the candidate to latest in the registry (no rebuild).')
    booleanParam(name: 'RUN_COMPOSE_E2E', defaultValue: false, description: 'Extra: docker compose up --build + ./scripts/test.sh against this commit (slow, needs free ports).')
    booleanParam(name: 'CLEAN_WS', defaultValue: false, description: 'Wipe the workspace afterwards (the buildx layer cache lives in the builder, not here).')
  }

  environment {
    ECOM_ROOT = "${WORKSPACE}"
    ECI_OUT_DIR = "${WORKSPACE}/.ci-output"
    ECOM_IMAGE_PREFIX = 'ecom'
    CI = 'true'
    // Isolated docker config per build: a shared ~/.docker across executors is how
    // one job ends up pushing to another job's registry.
    DOCKER_CONFIG = "${WORKSPACE}/.docker"
    BUILDER_NAME = 'ecom-buildx'
    DOCKERHUB_CREDS_ID = 'dockerhub-creds'
    GITOPS_CREDS_ID = 'gitops-token'
    GIT_AUTHOR_NAME = 'jenkins-ci'
    GIT_AUTHOR_EMAIL = 'jenkins@ecom.local'
    REGISTRY = "${params.REGISTRY}"
    PLATFORMS = "${params.PLATFORMS}"
    SKIP_MULTIARCH = params.PLATFORMS.contains(',') ? '0' : '1'
    ECOM_CI_SERVICES = "${params.SERVICES}"
    ECOM_BUILD_PARALLEL = '1'                  // the Jenkinsfile does its own fan-out
    ECOM_TEST_TIER = 'all'
    TRIVY_SEVERITY = "${params.TRIVY_SEVERITY}"
    TRIVY_MODE = params.SCAN_GATE ? 'gate' : 'report'
    TRIVY_TIMEOUT = '10m'
  }

  stages {

    stage('Prepare') {
      steps {
        checkout scm
        script {
          // Parameters are interpolated into shell commands below, so they are
          // whitelisted first. Cheap insurance against a job parameter turning into
          // a shell injection on a privileged build agent.
          def safe = [
            REGISTRY          : [params.REGISTRY,          ~/[A-Za-z0-9._:@\/ -]*/],
            PLATFORMS         : [params.PLATFORMS,         ~/[A-Za-z0-9,._@ \/-]+/],
            SERVICES          : [params.SERVICES,          ~/[A-Za-z0-9, _-]*/],
            IMAGE_TAG         : [params.IMAGE_TAG,         ~/[A-Za-z0-9._+-]*/],
            MAX_PARALLEL_BUILDS: [params.MAX_PARALLEL_BUILDS, ~/[0-9]+/],
            TRIVY_SEVERITY    : [params.TRIVY_SEVERITY,    ~/[A-Za-z,]+/],
            GITOPS_BRANCH     : [params.GITOPS_BRANCH,     ~/[A-Za-z0-9._\/-]+/],
          ]
          safe.each { name, spec ->
            def (value, re_) = spec
            if (value == null || !(value =~ re_)) {
              error("parameter ${name} contains unsupported characters: '${value}'")
            }
          }
          if ((params.MAX_PARALLEL_BUILDS as int) < 1 || (params.MAX_PARALLEL_BUILDS as int) > 10) {
            error("MAX_PARALLEL_BUILDS must be 1..10 (got ${params.MAX_PARALLEL_BUILDS})")
          }

          // ── build identity: one place, everything downstream reads env.* ──
          def sha = sh(script: 'git rev-parse --short=12 HEAD', returnStdout: true).trim()
          def branch = (env.BRANCH_NAME ?: sh(script: 'git rev-parse --abbrev-ref HEAD', returnStdout: true).trim()).replace('/', '-')
          def isPr = env.CHANGE_ID != null && env.CHANGE_ID.trim() != ''
          def isTag = env.TAG_NAME != null && env.TAG_NAME.trim() != ''
          def trunkNames = (env.ECOM_TRUNKS ?: 'main,master').split(',').collect { it.trim() }

          env.GIT_SHA12 = sha
          env.ECOM_BRANCH_TAG = isPr ? "pr-${env.CHANGE_ID}" : branch
          env.ECOM_TAG = (params.IMAGE_TAG?.trim()) ?: (isPr ? "pr-${env.CHANGE_ID}-${sha}" : "${branch}-${sha}")
          env.isPrBuild = isPr ? 'true' : 'false'
          env.isTagBuild = isTag ? 'true' : 'false'
          env.isTrunk = (!isPr && !isTag && trunkNames.contains(branch)) ? 'true' : 'false'
          // Pushing needs credentials; a PR from a fork has neither, so degrade to
          // build+smoke+scan instead of failing on the first 401.
          env.canPush = (params.PUSH_IMAGES && params.REGISTRY?.trim()) ? 'true' : 'false'
          env.GIT_TARGET_BRANCH = env.CHANGE_TARGET ?: 'main'

          currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.ECOM_TAG.take(24)}"
          currentBuild.description = "${params.PLATFORMS} · ${env.ECOM_TAG}" +
            (env.canPush == 'true' ? " → ${params.REGISTRY}" : ' (no push)')

          echo """build identity
  branch        : ${branch}   (trunk=${env.isTrunk} pr=${env.isPrBuild} tag=${env.isTagBuild})
  candidate tag : ${env.ECOM_TAG}
  registry      : ${params.REGISTRY ?: '<none — build/scan only>'}
  platforms     : ${params.PLATFORMS}
  services      : ${params.SERVICES}
  push/promote  : ${env.canPush} / ${params.PROMOTE_LATEST}
  gitops bump   : ${params.GITOPS_ENABLED} (branch ${params.GITOPS_BRANCH})"""
        }
        sh label: 'agent capability check', script: '''
          set -euo pipefail
          echo "=== agent toolchain ==="
          docker version --format 'docker      client {{.Client.Version}} / server {{.Server.Version}}' || echo "docker MISSING"
          docker buildx version 2>&1 | head -n1 || echo "buildx MISSING"
          python3 --version; git --version
          for t in trivy hadolint; do command -v "$t" >/dev/null 2>&1 && printf '%-12s %s\n' "$t" "$($t --version 2>&1 | head -n1)" || printf '%-12s MISSING\n' "$t"; done
          echo "=== docker entitlements ==="
          docker info --format 'storage-driver {{.Driver}} · cgroup {{.CgroupVersion}} · {{.NCPU}} cpu / {{.MemTotal}} bytes' 2>/dev/null || true
          mkdir -p "$ECI_OUT_DIR"
          { echo "## Phase 2 build $BUILD_NUMBER"; echo "- tag \`$ECOM_TAG\`"; echo "- platforms \`$PLATFORMS\`"; echo "- registry \`${REGISTRY:-<none>}\`"; } > "$ECI_OUT_DIR/summary.md"
        '''
        // Create the builder + register QEMU once, here, so ten parallel branches do
        // not race to bootstrap the same buildx instance (and fail as one).
        sh label: 'bootstrap buildx builder + binfmt', script: '''
          set -euo pipefail
          bash scripts/ci/build.sh --ensure-builder-only --builder "$BUILDER_NAME" || {
            echo "buildx builder could not start — see jenkins/setup.md §4 (privileged access / daemon socket)" >&2
            exit 3
          }
        '''
        // Registry login, scoped to this build's DOCKER_CONFIG, with the password on
        // stdin only. `docker login -p` would put the secret in the process list.
        withCredentials([usernamePassword(credentialsId: env.DOCKERHUB_CREDS_ID,
                                          usernameVariable: 'ECOM_REG_USER',
                                          passwordVariable: 'ECOM_REG_PASS')]) {
          sh label: 'docker login', script: '''
            set -euo pipefail
            if [ "${canPush}" = "true" ]; then
              host="${REGISTRY%%/*}"
              case "$host" in *.*) : ;; *) host="docker.io" ;; esac
              printf '%s' "$ECOM_REG_PASS" | docker login --username "$ECOM_REG_USER" --password-stdin "$host" >/dev/null
              echo "logged in to $host as $ECOM_REG_USER"
            else
              echo "push disabled for this build — skipping login"
            fi
          '''
        }
      }
    }

    stage('Lint') {
      steps {
        sh label: 'static analysis', script: '''
          set -euo pipefail
          bash scripts/ci/lint.sh
        '''
      }
      post {
        failure {
          echo 'Lint failed. Reproduce locally with `make ci-lint`; per-tool reports are in the archived .ci-output/.'
        }
      }
    }

    stage('Unit tests') {
      steps {
        // Deliberately executed inside python:3.12-slim with each service's own
        // requirements.txt: the interpreter that builds the images is the interpreter
        // that tests the code, so "works on the agent, fails in the image" cannot happen.
        sh label: 'pytest (structure + app tiers) in python:3.12-slim', script: '''
          set -euo pipefail
          ECOM_TEST_DOCKER=1 ECOM_TEST_TIER="$ECOM_TEST_TIER" bash scripts/ci/unit-tests.sh
        '''
        junit testResults: '.ci-output/junit-*.xml', allowEmptyResults: false, keepLongStdio: true
      }
    }

    stage('Build & push') {
      steps {
        script {
          def selected = sh(script: "bash scripts/ci/build.sh --list-services --services '${params.SERVICES}'",
                            returnStdout: true).trim().tokenize()
          if (!selected) {
            error "no services selected (SERVICES='${params.SERVICES}') — check the parameter and scripts/ci/lib/services.sh"
          }
          int maxPar = (params.MAX_PARALLEL_BUILDS ?: '4') as int
          env.SERVICE_COUNT = "${selected.size()}"
          echo "building ${selected.size()} service(s), ${maxPar} concurrently: ${selected.join(' ')}"

          selected.collate(maxPar).each { chunk ->
            def tasks = [:]
            chunk.each { String svc ->
              // One key per branch, and every value captured into a local: closures that
              // read the outer loop variable would all see the last service.
              String service = svc
              tasks["build ${service}"] = {
                stage("build ${service}") {
                  String imagesFile = "${env.ECI_OUT_DIR}/images-${service}.txt"
                  sh label: "build ${service} (${params.PLATFORMS})", script: """
                    set -euo pipefail
                    bash scripts/ci/build.sh \
                      --services ${service} --load \
                      --builder "\$BUILDER_NAME" \
                      --tag "\$ECOM_TAG" \
                      --images-file ${imagesFile}
                  """
                  if (params.RUN_SMOKE) {
                    // Empty --registry on purpose: the loaded image is local-only, the
                    // registry name would force a pull of something that may not exist yet.
                    sh label: "smoke ${service}", script: """
                      set -euo pipefail
                      bash scripts/ci/smoke.sh --services ${service} --tag "\$ECOM_TAG" --registry '' --timeout 90
                    """
                  }
                  if (env.canPush == 'true') {
                    retry(2) {                       // registry 5xx and blob upload
                      timeout(time: 25, unit: 'MINUTES') {   // resets are routine at 3am
                        sh label: "push ${service} → ${params.REGISTRY}", script: """
                          set -euo pipefail
                          # Same builder, same inputs: the layers are already built, so this
                          # uploads the multi-arch manifest instead of recompiling.
                          bash scripts/ci/build.sh \
                            --services ${service} --push \
                            --platforms "\$PLATFORMS" \
                            --registry "\$REGISTRY" \
                            --builder "\$BUILDER_NAME" \
                            --cache \
                            --tag "\$ECOM_TAG" --extra-tag "\$ECOM_BRANCH_TAG" \
                            --images-file ${imagesFile}
                        """
                      }
                    }
                  } else {
                    echo "push skipped for ${service} (PUSH_IMAGES/REGISTRY off) — candidate tag exists only in the build log"
                  }
                }
              }
            }
            parallel tasks
          }

          // Merge the per-service manifests in registry order: deterministic diff,
          // and gitops-bump.sh gets exactly one line per built service.
          sh label: 'collect image manifest', script: '''
            set -euo pipefail
            : > "$ECI_OUT_DIR/images.txt"
            for s in $(bash scripts/ci/build.sh --list-services --services "$ECOM_CI_SERVICES"); do
              f="$ECI_OUT_DIR/images-$s.txt"
              [ -s "$f" ] && cat "$f" >> "$ECI_OUT_DIR/images.txt"
            done
            [ -s "$ECI_OUT_DIR/images.txt" ] || { echo "no image manifest produced" >&2; exit 1; }
            echo "--- images ---"; column -t "$ECI_OUT_DIR/images.txt" 2>/dev/null || cat "$ECI_OUT_DIR/images.txt"
            lines=$(grep -c . "$ECI_OUT_DIR/images.txt")
            if [ "$lines" != "$SERVICE_COUNT" ]; then
              echo "!! expected $SERVICE_COUNT manifest lines, got $lines — a branch wrote to the wrong file" >&2
              exit 1
            fi
            if grep -q $'\\t-\$' "$ECI_OUT_DIR/images.txt"; then
              echo "note: some images have no registry digest (push disabled) — the bump will leave digest alone"
            fi
          '''
        }
      }
    }

    stage('Trivy security gate') {
      steps {
        script {
          // Warm the vulnerability DB once. Ten parallel trivies each fetching ~60 MB
          // is the classic "why is CI slow and flaky" answer.
          sh label: 'warm trivy DB', script: '''
            set -euo pipefail
            bash scripts/ci/scan.sh --download-db-only || {
              echo "trivy DB download failed (no egress to ghcr.io?) — failing rather than skipping the gate" >&2
              exit 3
            }
          '''
          def selected = sh(script: "bash scripts/ci/build.sh --list-services --services '${params.SERVICES}'",
                            returnStdout: true).trim().tokenize()
          int maxPar = (params.MAX_PARALLEL_BUILDS ?: '4') as int

          selected.collate(maxPar).each { chunk ->
            def tasks = [:]
            chunk.each { String svc ->
              String service = svc
              tasks["scan ${service}"] = {
                stage("scan ${service}") {
                  // catchError keeps the fan-out honest: every service gets scanned and
                  // reported, instead of the first finding cancelling its siblings.
                  // Scanning the pushed candidate is a stronger claim than scanning a
                  // local tarball: it is the bytes the cluster will actually pull.
                  String target = env.canPush == 'true'
                    ? "--services ${service} --registry "\$REGISTRY""
                    : "--image ${env.ECOM_IMAGE_PREFIX}-${service}:"\$ECOM_TAG""
                  catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh label: "trivy ${service}", script: """
                      set -euo pipefail
                      bash scripts/ci/scan.sh ${target} --tag "\$ECOM_TAG" \
                        --severity "\$TRIVY_SEVERITY" --mode "\$TRIVY_MODE" \
                        --out-dir "\$ECI_OUT_DIR/trivy-${service}"
                      touch "\$ECI_OUT_DIR/scan-ok-${service}"
                    """
                  }
                }
              }
            }
            parallel tasks
          }

          def blocked = selected.findAll { String svc ->
            sh(script: "test -f .ci-output/scan-ok-${svc}", returnStatus: true) != 0
          }
          env.TRIVY_BLOCKED = blocked.join(',')
          if (blocked) {
            def hint = 'reports: .ci-output/trivy-*/ (junit + sarif + table). To accept a finding: add its CVE to .trivyignore with an owner and a re-check date.'
            if (params.SCAN_GATE) {
              error("trivy gate blocked: ${blocked.join(', ')} — ${hint}")
            }
            unstable("trivy findings on ${blocked.join(', ')} (SCAN_GATE=false → report only) — ${hint}")
          } else {
            echo "trivy: no ${params.TRIVY_SEVERITY} findings on ${selected.size()} image(s)"
          }
        }
      }
      post {
        always {
          junit testResults: '.ci-output/trivy-*/junit-trivy.xml', allowEmptyResults: true, keepLongStdio: true
        }
      }
    }

    stage('Promote images') {
      when { expression { params.PROMOTE_LATEST && env.canPush == 'true' } }
      steps {
        sh label: 'retag candidate → latest', script: '''
          set -euo pipefail
          # imagetools create writes a new manifest pointing at the *same* multi-arch
          # manifest that just passed the gate: no rebuild, so a promoted tag can never
          # be "the same version but slightly different bytes".
          bash scripts/ci/build.sh --promote --registry "$REGISTRY" --tag "$ECOM_TAG" --to latest \
            --services "$ECOM_CI_SERVICES"
        '''
      }
    }

    stage('GitOps bump') {
      // Trunk only, never a PR/tag build, and only when images actually landed in a
      // registry — otherwise we would advertise a rollout that cannot be pulled.
      when {
        expression { params.GITOPS_ENABLED && env.canPush == 'true' && env.isTrunk == 'true' }
        beforeAgent true
      }
      steps {
        withCredentials([usernamePassword(credentialsId: env.GITOPS_CREDS_ID,
                                          usernameVariable: 'GIT_USER',
                                          passwordVariable: 'GIT_TOKEN')]) {
          // Single-quoted on purpose: everything below is expanded by the shell, and
          // GITOPS_BRANCH/GIT_TARGET_BRANCH arrive as Jenkins env vars (parameters are
          // exported). No Groovy interpolation inside a shell command that carries a
          // token — a quote in a parameter cannot restructure the command line.
          sh label: 'bump helm image tags + push gitops branch', script: '''
            set -euo pipefail
            slug=$(git config --get remote.origin.url | sed -E 's#^(https://|git@)([^@]*@)?github\\.com[:/]##; s#\\.git$##')
            [ -n "$slug" ] || { echo "no github origin to push to — skipping bump"; exit 0; }
            # The token reaches git only through this command's environment;
            # gitops-bump.sh redacts credentials before it logs the push URL.
            export GIT_REMOTE_URL="https://${GIT_USER}:${GIT_TOKEN}@github.com/${slug}.git"
            export GITOPS_BASE="${GIT_TARGET_BRANCH:-main}"
            bash scripts/ci/gitops-bump.sh --images-file "$ECI_OUT_DIR/images.txt" --push \
              --branch "${GITOPS_BRANCH:-gitops/main}" --base "$GITOPS_BASE"
          '''
        }
      }
      post {
        success { echo 'Values bumped → ArgoCD (Phase 5) reconciles. Verify with: argocd app list' }
        failure { echo 'Bump failed. Usual causes: GITOPS_BRANCH equals the base branch, the gitops-token lacks write scope, or a values file has a hand-edited image block (the bump refuses to guess).' }
      }
    }

    stage('E2E smoke test') {
      // Off by default: this is Phase 1 Compose topology, which needs Postgres,
      // Redis and RabbitMQ plus 13 free ports on the agent. Phase 4 replaces it with
      // an ephemeral Kind cluster, where the same ./scripts/test.sh runs against Helm.
      when { expression { params.RUN_COMPOSE_E2E } }
      steps {
        sh label: 'compose up + scripts/test.sh', script: '''
          set -euo pipefail
          docker compose -f docker-compose.yaml up -d --build --wait --wait-timeout 240
          ./scripts/test.sh
        '''
      }
      post {
        always {
          sh label: 'compose down', script: 'docker compose -f docker-compose.yaml down -v --remove-orphans || true', returnStatus: true
        }
      }
    }
  }

  post {
    always {
      script {
        // Publish/archive before any workspace cleanup, then drop registry creds.
        junit testResults: '.ci-output/junit-*.xml,.ci-output/trivy-*/junit-trivy.xml',
              allowEmptyResults: true, keepLongStdio: true
        archiveArtifacts artifacts: '.ci-output/**',
                         allowEmptyArchive: true,
                         fingerprint: false,
                         excludes: '.ci-output/*.log'
        sh label: 'docker logout', script: '''
          host="${REGISTRY:-docker.io}"; host="${host%%/*}"
          case "$host" in *.*) : ;; *) host="docker.io" ;; esac
          docker logout "$host" 2>/dev/null || true
        ''', returnStatus: true
        if (params.CLEAN_WS) {
          cleanWs deleteDirs: true, notFailBuild: true,
                  patterns: [[pattern: '.docker/**', type: 'INCLUDE']]
        }
      }
    }
    success {
      script {
        currentBuild.description = "${env.SERVICE_COUNT ?: '?'} images · ${params.PLATFORMS} · ${env.ECOM_TAG}"
        echo "GREEN — ${env.ECOM_TAG}. ${env.isTrunk == 'true' && params.GITOPS_ENABLED ? 'helm values bumped; ArgoCD owns the rollout.' : 'no GitOps bump (non-trunk or GITOPS_ENABLED=false).'}"
      }
    }
    unstable { echo 'UNSTABLE — trivy reported findings while the gate was off (SCAN_GATE=false), or a non-blocking tool was missing.' }
    failure { echo "FAILED — read .ci-output/summary.md in the artifacts, then reproduce locally: bash scripts/ci/all.sh --keep-going" }
  }
}

// ── Deliberately NOT in this pipeline ────────────────────────────────────────
//  • kubectl/helm/argocd: deployments are Phase 4/5, and CI that can deploy is a
//    credential with too much reach for a build box.
//  • `docker compose up` in the main path: it re-builds what buildx just built.
//  • A hardcoded service matrix: it would drift from scripts/ci/lib/services.sh
//    the moment an 11th service lands.
//  • `--load` + multi-platform: buildx rejects it, which is why the smoke test runs
//    against a single-platform load and the push re-uses the same builder cache.
