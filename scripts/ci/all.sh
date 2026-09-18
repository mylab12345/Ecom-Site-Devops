#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/ci/all.sh — run the whole Phase 2 pipeline locally, in order, using
# the very same scripts Jenkins calls. "It works on my machine" means exactly
# this: `scripts/ci/all.sh` green ⇒ every pipeline stage will pass.
#
#   scripts/ci/all.sh                     # preflight → lint → test → build → smoke → scan → gitops
#   scripts/ci/all.sh --stage lint        # one stage
#   scripts/ci/all.sh --from scan         # resume after fixing something
#   scripts/ci/all.sh --skip smoke --keep-going
#   scripts/ci/all.sh --doctor            # toolchain report only
#   scripts/ci/all.sh --registry docker.io/myhub --push --multi-arch
#
# Flags
#   --stage NAME      run exactly one stage
#   --from NAME       run NAME and every stage after it
#   --skip NAME       repeatable
#   --push            build with --push (needs a logged-in registry)
#   --registry R      hub org or host:5000/local  (implies --push)
#   --tag T           default: git short SHA
#   --services LIST   default: all
#   --multi-arch      build linux/amd64,linux/arm64 (needs buildx + QEMU/binfmt)
#   --keep-going      keep running later stages after a failure
#   --gitops-push     let the gitops stage commit + push (default: --dry-run)
#   --report FILE     markdown summary            [default .ci-output/pipeline.md]
#
# Exit: 0 green · 1 a stage failed · 3 preflight problem (missing toolchain)
# ---------------------------------------------------------------------------
set -uo pipefail

# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

CI_DIR="$ECOM_ROOT/scripts/ci"
export CI_DIR

STAGES=(preflight lint test build smoke scan gitops)
ONE=""; FROM=""; SKIP=(); KEEP_GOING=0
PUSH=0; REGISTRY="${REGISTRY:-}"; TAG="${IMAGE_TAG:-}"; SERVICES="all"
MULTI_ARCH=0; GITOPS_PUSH=0; REPORT=""; SCAN_MODE="${TRIVY_MODE:-report}"
EXTRA_TAG="${EXTRA_TAG:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)      ONE="${2-}"; shift 2 ;;
    --from)       FROM="${2-}"; shift 2 ;;
    --skip)       SKIP+=("$2"); shift 2 ;;
    --push)       PUSH=1; shift ;;
    --registry)   REGISTRY="$2"; PUSH=1; shift 2 ;;
    --tag)        TAG="${2-}"; shift 2 ;;
    --services)   SERVICES="${2-}"; shift 2 ;;
    --multi-arch) MULTI_ARCH=1; shift ;;
    --gitops-push) GITOPS_PUSH=1; shift ;;
    --keep-going) KEEP_GOING=1; shift ;;
    --report)     REPORT="${2-}"; shift 2 ;;
    --doctor)     ONE="preflight"; shift ;;
    -h|--help)    ecom_ci_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    *)            eci_die "all.sh: unknown argument: $1" ;;
  esac
done

validate_stage() {
  local name="$1" s
  for s in "${STAGES[@]}"; do [[ "$s" == "$name" ]] && return 0; done
  eci_die "all.sh: unknown stage '$name' (one of: ${STAGES[*]})"
}
if [[ -n "$ONE" ]]; then validate_stage "$ONE"; fi
if [[ -n "$FROM" ]]; then validate_stage "$FROM"; fi
for s in ${SKIP[@]+"${SKIP[@]}"}; do validate_stage "$s"; done

mkdir -p "$ECI_OUT_DIR"
REPORT="${REPORT:-$ECI_OUT_DIR/pipeline.md}"
: > "$REPORT"

should_run() { # <stage> → 0 = run it
  local name="$1" i start=0 idx=-1
  [[ -n "$ONE" ]] && { [[ "$ONE" == "$name" ]] || return 1; }
  if [[ -n "$FROM" ]]; then
    for i in "${!STAGES[@]}"; do
      [[ "${STAGES[i]}" == "$FROM" ]] && start=$i
    done
  fi
  for i in "${!STAGES[@]}"; do [[ "${STAGES[i]}" == "$name" ]] && idx=$i; done
  (( idx < start )) && return 1
  local k
  for k in "${SKIP[@]+"${SKIP[@]}"}"; do [[ "$k" == "$name" ]] && return 1; done
  return 0
}

declare -A RESULT=()
ORDER=()
FAILED=0
T_START=$(date +%s)

run_stage() { # <name> <desc> <fn>
  local name="$1" desc="$2" fn="$3"
  ORDER+=("$name")
  if ! should_run "$name"; then
    RESULT["$name"]="skipped"
    return 0
  fi
  echo
  ecom_ci_hdr "$name — $desc"
  local t0 rc
  t0=$(date +%s)
  "$fn"; rc=$?
  local dur=$(( $(date +%s) - t0 ))
  if (( rc == 0 )); then
    RESULT["$name"]="pass (${dur}s)"
    printf -- '- ✅ **%s** — %s · %ss\n' "$name" "$desc" "$dur" >> "$REPORT"
  elif (( rc == 3 )); then
    RESULT["$name"]="toolchain problem (${dur}s)"
    printf -- '- ⚠️ **%s** — toolchain problem · %ss\n' "$name" "$dur" >> "$REPORT"
    FAILED=1
    [[ "$KEEP_GOING" == "1" ]] || summary_and_exit
  else
    RESULT["$name"]="failed rc=$rc (${dur}s)"
    printf -- '- ❌ **%s** — %s · rc=%s · %ss\n' "$name" "$desc" "$rc" "$dur" >> "$REPORT"
    FAILED=1
    [[ "$KEEP_GOING" == "1" ]] || summary_and_exit
  fi
  return 0
}

summary_and_exit() {
  echo
  ecom_ci_hdr "pipeline summary — $(( $(date +%s) - T_START ))s total"
  local name
  for name in "${ORDER[@]}"; do
    printf '  %-10s %s\n' "$name" "${RESULT[$name]}"
  done
  {
    echo
    echo "_artifacts: \`$ECI_OUT_DIR\` (junit-*.xml, trivy/, images.txt, build-*.log)_"
    if [[ -f "$ECI_OUT_DIR/images.txt" ]]; then
      echo
      echo '```'
      column -t "$ECI_OUT_DIR/images.txt" 2>/dev/null || cat "$ECI_OUT_DIR/images.txt"
      echo '```'
    fi
  } >> "$REPORT"
  ecom_ci_log "report: $REPORT"
  if (( FAILED )); then
    ecom_ci_err "pipeline failed — jenkins/setup.md §9 lists the usual causes"
    exit 1
  fi
  ecom_ci_ok "pipeline green"
}

# ── stages ───────────────────────────────────────────────────────────────────
probe() { # <label> <cmd...>
  local label="$1"; shift
  if command -v "$1" >/dev/null 2>&1; then
    printf '  %-12s %s\n' "$label" "$("$@" 2>&1 | head -n1)"
    return 0
  fi
  printf '  %-12s %s\n' "$label" "not installed"
  return 1
}

stage_preflight() {
  echo "  tool          version / status"
  echo "  ------------  -------------------------------------"
  local missing_opt=0
  probe python3 python3 --version || return 3
  probe git git --version || return 3
  probe docker docker --version || missing_opt=1
  probe buildx docker buildx version || missing_opt=1
  probe trivy trivy --version || missing_opt=1
  probe hadolint hadolint --version || missing_opt=1
  probe shellcheck shellcheck --version || missing_opt=1
  probe yq yq --version || missing_opt=1
  echo
  ecom_ci_log "host platform : $(ecom_ci_host_platform)"
  ecom_ci_log "ci detected   : $(ecom_ci_is_ci && echo yes || echo no)"
  ecom_ci_log "workspace     : $ECOM_ROOT"
  ecom_ci_log "artifacts     : $ECI_OUT_DIR"
  if [[ "$PUSH" == "1" ]]; then
    have docker || { ecom_ci_err "--push needs docker"; return 3; }
    if ! docker info >/dev/null 2>&1; then
      ecom_ci_err "docker daemon unreachable (Linux: sudo usermod -aG docker \$USER, then re-login)"
      return 3
    fi
    [[ -n "$REGISTRY" ]] || { ecom_ci_err "--push needs --registry"; return 3; }
    ecom_ci_log "registry      : $REGISTRY"
  fi
  if have git && [[ -n "$(git -C "$ECOM_ROOT" status --porcelain 2>/dev/null)" ]]; then
    ecom_ci_warn "dirty working tree — image tags get a '-dirty' suffix (build.sh)"
  fi
  (( missing_opt )) && ecom_ci_log "optional tools missing — jenkins/setup.md §2-§5 has install commands"
  return 0
}

stage_lint()  { bash "$CI_DIR/lint.sh"; }
stage_test()  { bash "$CI_DIR/unit-tests.sh" --tier "${ECOM_TEST_TIER:-auto}"; }
stage_build() {
  local args=(--services "$SERVICES")
  [[ -n "$TAG" ]] && args+=(--tag "$TAG")
  if [[ "$PUSH" == "1" ]]; then
    args+=(--push --registry "$REGISTRY" --cache)
    [[ -n "$EXTRA_TAG" ]] && args+=(--extra-tag "$EXTRA_TAG")
  else
    args+=(--load)
  fi
  if [[ "$MULTI_ARCH" == "1" ]]; then
    args+=(--platforms linux/amd64,linux/arm64)
  else
    args+=(--platforms "$(ecom_ci_host_platform)")
  fi
  bash "$CI_DIR/build.sh" "${args[@]}"
}
stage_smoke() {
  local args=(--services "$SERVICES")
  [[ -n "$TAG" ]] && args+=(--tag "$TAG")
  [[ "$PUSH" == "1" && -n "$REGISTRY" ]] && args+=(--registry "$REGISTRY")
  bash "$CI_DIR/smoke.sh" "${args[@]}"
}
stage_scan() {
  local args=(--services "$SERVICES" --mode "$SCAN_MODE" --fs)
  [[ -n "$TAG" ]] && args+=(--tag "$TAG")
  [[ -n "$REGISTRY" ]] && args+=(--registry "$REGISTRY")
  bash "$CI_DIR/scan.sh" "${args[@]}"
}
stage_gitops() {
  local args=()
  if [[ "$PUSH" == "1" && "$GITOPS_PUSH" == "1" ]]; then
    args+=(--commit)
    [[ "${GITOPS_DO_PUSH:-0}" == "1" ]] && args+=(--push)
  else
    args+=(--dry-run)
  fi
  bash "$CI_DIR/gitops-bump.sh" --images-file "$ECI_OUT_DIR/images.txt" "${args[@]}"
}

run_stage preflight "toolchain + registry + git state"  stage_preflight
run_stage lint      "ruff, hadolint, compose & structure" stage_lint
run_stage test      "pytest (structure + app tiers)"    stage_test
run_stage build     "buildx images"                     stage_build
run_stage smoke     "each image answers /health"        stage_smoke
run_stage scan      "trivy CVE gate"                    stage_scan
run_stage gitops    "helm values tag bump"              stage_gitops

summary_and_exit
