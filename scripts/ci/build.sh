#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/ci/build.sh — multi-arch image builds (docker buildx) for the 10
# services: the heart of the Phase 2 pipeline. Used by Jenkins for the
# build+push, and locally for `make ci-build` validation runs.
#
#   scripts/ci/build.sh                       # local: build+load host arch, no push
#   scripts/ci/build.sh --push                # multi-arch build & push to registry
#   scripts/ci/build.sh --print-plan          # show the resolved matrix, build nothing
#   scripts/ci/build.sh --services product,gateway --platforms linux/amd64
#   SKIP_MULTIARCH=1 scripts/ci/build.sh      # CI escape hatch: amd64-only
#
# Flags
#   --services LIST     all | auto (only changed) | comma list        [default all]
#   --registry NAME     hub org or host/namespace ("" → local-only, no push)
#   --tag TAG           default: git short SHA (or $IMAGE_TAG)
#   --extra-tag TAG     repeatable (latest, main, pr-123 …)
#   --platforms LIST    default linux/amd64,linux/arm64
#   --push | --load     --load forces single platform (buildx cannot --load a manifest list)
#   --builder NAME      buildx builder to create/reuse          [default ecom-buildx]
#   --parallel N        concurrent service builds               [default CI:4 local:2]
#   --cache             registry build cache (push mode, <image>:buildcache)
#   --promote           retag TAG → --to TAG in the registry (no rebuild) for scanning-after
#   --to TAG            destination tag for --promote           [default latest]
#   --images-file F     where to record svc<TAB>ref<TAB>digest  [.ci-output/images.txt]
#                       (Jenkins passes one per service: parallel branches must
#                        not append to the same file)
#   --list-services     print the resolved service names and exit (no docker needed)
#   --ensure-builder-only  create/bootstrap the buildx builder, build nothing
#   --dry-run           log commands instead of running them
#
# Artifacts
#   images.txt (or --images-file)  svc <TAB> ref <TAB> digest → gitops-bump.sh
#   .ci-output/build-<svc>.log  per-service build log (archived by Jenkins)
#
# Exit: 0 ok · 1 build failure · 3 environment problem (no docker/buildx)
# ---------------------------------------------------------------------------
set -uo pipefail

# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
# shellcheck source=lib/services.sh
source "$ECI_LIB_DIR/services.sh"

SERVICES="${ECOM_CI_SERVICES:-all}"
REGISTRY="${REGISTRY:-}"
TAG="${IMAGE_TAG:-}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
EXTRA_TAGS=()
MODE=""
BUILDER="${BUILDER_NAME:-ecom-buildx}"
PARALLEL="${ECOM_BUILD_PARALLEL:-}"
USE_CACHE="${ECOM_BUILD_CACHE:-0}"
DRY_RUN="${DRY_RUN:-0}"
PRINT_PLAN=0
LIST_ONLY=0
ENSURE_BUILDER_ONLY=0
IMAGES_FILE=""
PROVENANCE="${ECOM_PROVENANCE:-false}"
SKIP_MULTIARCH="${SKIP_MULTIARCH:-0}"
PROMOTE=0
PROMOTE_TO="${ECOM_PROMOTE_TO:-latest}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --services)   SERVICES="${2-}"; shift 2 ;;
    --registry)   REGISTRY="${2-}"; shift 2 ;;
    --tag)        TAG="${2-}"; shift 2 ;;
    --platforms)  PLATFORMS="${2-}"; shift 2 ;;
    --extra-tag)  EXTRA_TAGS+=("${2:?missing value}"); shift 2 ;;
    --push)       MODE=push; shift ;;
    --load)       MODE=load; shift ;;
    --builder)    BUILDER="${2-}"; shift 2 ;;
    --parallel)   PARALLEL="${2-}"; shift 2 ;;
    --cache)      USE_CACHE=1; shift ;;
    --promote)    PROMOTE=1; shift ;;
    --to)         PROMOTE_TO="${2-}"; shift 2 ;;
    --dry-run)    DRY_RUN=1; export DRY_RUN; shift ;;
    --print-plan) PRINT_PLAN=1; shift ;;
    --list-services) LIST_ONLY=1; shift ;;
    --ensure-builder-only) ENSURE_BUILDER_ONLY=1; shift ;;
    --images-file) IMAGES_FILE="${2-}"; shift 2 ;;
    -h|--help)    ecom_ci_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    *)            eci_die "build.sh: unknown argument: $1" ;;
  esac
done

[[ "$SKIP_MULTIARCH" == "1" ]] && PLATFORMS="linux/amd64"
[[ -n "$MODE" ]] || { [[ -n "$REGISTRY" ]] && MODE=push || MODE=load; }
[[ -n "$PARALLEL" ]] || { ecom_ci_is_ci && PARALLEL=4 || PARALLEL=2; }

if [[ -z "$TAG" ]]; then
  if have git && git -C "$ECOM_ROOT" rev-parse --short=12 HEAD >/dev/null 2>&1; then
    TAG="$(git -C "$ECOM_ROOT" rev-parse --short=12 HEAD)"
    # A dirty tree must never masquerade as a clean SHA (Phase 4 rollback safety).
    git -C "$ECOM_ROOT" diff --quiet HEAD -- . 2>/dev/null || TAG="${TAG}-dirty"
  else
    TAG="dev-$(date -u +%Y%m%d)"
  fi
fi

# Resolve the selection in the *current* shell so an unknown service name aborts
# the whole build instead of dying inside a process-substitution subshell.
selection="$(ecom_ci_select "$SERVICES")" || eci_die "build.sh: bad --services '$SERVICES'"
mapfile -t LIST < <(printf '%s\n' "$selection" | tr ' ' '\n' | grep -v '^$' || true)
if (( LIST_ONLY )); then
  (( ${#LIST[@]} > 0 )) || eci_die "build.sh: bad --services '$SERVICES'"
  printf '%s\n' "${LIST[@]}"
  exit 0
fi
(( ${#LIST[@]} > 0 )) || eci_die "build.sh: no services selected (known: $(ecom_ci_services | tr '\n' ' '))"
IMAGES_FILE="${IMAGES_FILE:-$ECI_OUT_DIR/images.txt}"
mkdir -p "$(dirname "$IMAGES_FILE")"

MULTIPLATFORM=0
[[ "$PLATFORMS" == *,* ]] && MULTIPLATFORM=1
if [[ "$MULTIPLATFORM" == "0" && "$PLATFORMS" != *,* ]]; then
  : # single platform: no QEMU/binfmt needed, docker driver may be used for --load
fi

# --load cannot carry a manifest list: narrow the platform to the host arch.
if [[ "$MODE" == "load" ]] && (( MULTIPLATFORM )); then
  ecom_ci_warn "--load does not support multi-platform — narrowing to $(ecom_ci_host_platform)"
  PLATFORMS="$(ecom_ci_host_platform)"
  MULTIPLATFORM=0
fi

tags_for() { # <service> → one image ref per tag
  local svc="$1"
  ecom_ci_image_ref "$svc" "$TAG" "$REGISTRY"
  local t
  for t in ${EXTRA_TAGS[@]+"${EXTRA_TAGS[@]}"}; do
    ecom_ci_image_ref "$svc" "$t" "$REGISTRY"
  done
}

ecom_ci_log "registry=${REGISTRY:-<local>} tag=$TAG mode=$MODE platforms=$PLATFORMS parallel=$PARALLEL"
ecom_ci_log "services: ${LIST[*]}"

if (( PROMOTE )); then
  # Promotion, not rebuild: `imagetools create` writes a new manifest that points
  # at the already-scanned multi-arch manifest, so `latest` can never reference a
  # half-built image and the amd64+arm64 pair stays byte-identical to what passed
  # the Trivy gate.
  [[ -n "$REGISTRY" ]] || { ecom_ci_err "--promote needs --registry (nothing to retag in a local-only build)"; exit 3; }
  ecom_ci_require docker "docker buildx imagetools create is used to retag in the registry"
  promoted=()
  for svc in "${LIST[@]}"; do
    src="$(ecom_ci_image_ref "$svc" "$TAG" "$REGISTRY")"
    dst="$(ecom_ci_image_ref "$svc" "$PROMOTE_TO" "$REGISTRY")"
    ecom_ci_log "promoting $src → $dst"
    if ecom_ci_run docker buildx imagetools create --tag "$dst" "$src"; then
      promoted+=("$svc")
      printf '%s\t%s\t%s\n' "$svc" "$dst" "-" >> "$(ecom_ci_out images-$PROMOTE_TO.txt)"
      ecom_ci_summary "🏷️ \`$svc\` promoted \`$TAG\` → \`$PROMOTE_TO\`"
    else
      ecom_ci_err "promotion failed for $svc ($src not found in the registry?)"
      exit 1
    fi
  done
  ecom_ci_ok "promoted ${#promoted[@]} image(s) to tag '$PROMOTE_TO'"
  exit 0
fi

if (( PRINT_PLAN )); then
  echo
  printf '%-14s %-6s %-28s %s\n' SERVICE PORT PLATFORMS PRIMARY_IMAGE
  printf '%-14s %-6s %-28s %s\n' ------- ---- ---------------- -------------
  for svc in "${LIST[@]}"; do
    printf '%-14s %-6s %-28s %s\n' "$svc" "$(ecom_ci_port "$svc")" "$PLATFORMS" \
      "$(ecom_ci_image_ref "$svc" "$TAG" "$REGISTRY")"
  done
  echo
  ecom_ci_log "builder=${BUILDER} · cache=$([[ "$USE_CACHE" == "1" ]] && echo registry || echo off) · provenance=$PROVENANCE · mode=$MODE"
  exit 0
fi

ecom_ci_require docker "install Docker Engine 24+ (README §3)"
docker buildx version >/dev/null 2>&1 || {
  ecom_ci_err "docker buildx is not available — install buildx 0.13+ (jenkins/setup.md §3)"
  exit 3
}

mkdir -p "$(ecom_ci_out "")"

# --- builder ---------------------------------------------------------------
# Multi-platform or push needs the docker-container driver; a single-platform
# --load is faster on the default (docker) driver, which also shares the local
# image store so scripts/ci/smoke.sh can run the result without an export.
BUILDER_ARGS=()
ensure_builder() {
  if (( ! MULTIPLATFORM )) && [[ "$MODE" == "load" ]]; then
    ecom_ci_log "single-platform --load: using the default docker driver"
    return 0
  fi
  if docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
    ecom_ci_log "reusing buildx builder '$BUILDER'"
  else
    ecom_ci_log "creating buildx builder '$BUILDER' (docker-container driver)"
    ecom_ci_run docker buildx create --name "$BUILDER" --driver docker-container \
      --driver-opt network=host --use --bootstrap || {
        ecom_ci_err "buildx builder bootstrap failed — is the Docker daemon running?"
        exit 3
      }
    ecom_ci_summary "🧱 buildx builder \`$BUILDER\` (docker-container driver)"
  fi
  BUILDER_ARGS=(--builder "$BUILDER")
}

ensure_binfmt() {
  [[ "$PLATFORMS" == *arm64* || "$PLATFORMS" == *arm/v7* ]] || return 0
  if [[ "$(ecom_ci_host_platform)" == *arm64 ]]; then
    ecom_ci_log "host is arm64 — native, no QEMU needed"
    return 0
  fi
  [[ "${ECOM_SKIP_BINFMT:-0}" == "1" ]] && {
    ecom_ci_warn "ECOM_SKIP_BINFMT=1 — assuming binfmt/QEMU is preinstalled"; return 0; }
  ecom_ci_log "registering QEMU binfmt for linux/arm64 (needed on amd64 hosts)"
  if ! ecom_ci_run docker run --privileged --rm tonistiigi/binfmt --install arm64; then
    if ecom_ci_is_ci; then
      ecom_ci_err "binfmt registration failed — the buildx agent needs privileged access once (jenkins/setup.md §4)"
      exit 3
    fi
    ecom_ci_warn "binfmt registration failed — continuing (the host may already emulate arm64)"
  fi
}

ensure_builder
ensure_binfmt
if (( ENSURE_BUILDER_ONLY )); then
  ecom_ci_ok "buildx builder ready: ${BUILDER:-docker (default driver)}"
  exit 0
fi

: > "$IMAGES_FILE"

# --- per-service build ------------------------------------------------------
build_one() {
  local svc="$1"
  local ctx df first ref base
  ctx="$(ecom_ci_context "$svc")"
  df="$(ecom_ci_dockerfile "$svc")"
  first="$(ecom_ci_image_ref "$svc" "$TAG" "$REGISTRY")"
  ref="$first"
  base="${ref%:*}"

  local args=(buildx build ${BUILDER_ARGS[@]+"${BUILDER_ARGS[@]}"})
  args+=(--platform "$PLATFORMS")
  local t
  while IFS= read -r t; do args+=(-t "$t"); done < <(tags_for "$svc")

  if [[ "$USE_CACHE" == "1" && "$MODE" == "push" ]]; then
    args+=(--cache-from "type=registry,ref=${base}:buildcache")
    args+=(--cache-to "type=registry,ref=${base}:buildcache,mode=max,image-manifest=true,oci-mediatypes=true")
  fi
  # --provenance/--sbom create an attestation manifest that some registries
  # (and our Phase 4 digest pinning) handle poorly; re-enable via ECOM_PROVENANCE.
  args+=(--provenance="$PROVENANCE" --sbom=false)
  args+=(--metadata-file "$(ecom_ci_out "meta-$svc.json")")
  [[ "$MODE" == "push" ]] && args+=(--push) || args+=(--load)
  args+=(-f "$df" "$ctx")

  printf '\n=== [%s] %s — %s (%s) ===\n' "$(date -u +%H:%M:%S)" "$svc" "$MODE" "$PLATFORMS"
  local rc=0
  ecom_ci_run docker "${args[@]}" || rc=$?
  if (( rc != 0 )); then
    printf '!! [%s] build failed (rc=%s)\n' "$svc" "$rc"
    return "$rc"
  fi

  # Digest: recorded only when the manifest actually lives in a registry.
  local digest="-"
  if [[ "$MODE" == "push" && -s "$(ecom_ci_out "meta-$svc.json")" ]] && have python3; then
    digest="$(ECI_OUT_DIR="$ECI_OUT_DIR" python3 - "$svc" <<'PY'
import json, os, sys
path = os.path.join(os.environ["ECI_OUT_DIR"], f"meta-{sys.argv[1]}.json")
try:
    print(json.load(open(path)).get("containerimage.digest", "-"))
except Exception:
    print("-")
PY
)"
  fi
  printf '%s\t%s\t%s\n' "$svc" "$ref" "$digest" >> "$IMAGES_FILE"
  printf '✓ [%s] %s %s\n' "$svc" "$ref" \
    "$([[ "$digest" == "-" ]] && echo "(available locally)" || printf '@%s' "${digest:0:19}")"
  return 0
}

# --- chunked parallel execution (keeps daemon load bounded) ---------------
PIDS=(); SVC_OF_PID=(); LOG_OF_PID=()
for svc in "${LIST[@]}"; do
  while (( $(jobs -rp | wc -l) >= PARALLEL )); do sleep 1; done
  log="$(ecom_ci_out "build-$svc.log")"
  ( build_one "$svc" ) > "$log" 2>&1 &
  pid=$!
  PIDS+=("$pid"); SVC_OF_PID+=("$svc"); LOG_OF_PID+=("$log")
done

FAILURES=()
if (( ${#PIDS[@]} )); then
  ecom_ci_log "waiting for ${#PIDS[@]} build(s) (max $PARALLEL concurrent)…"
  for i in "${!PIDS[@]}"; do
    if wait "${PIDS[$i]}"; then
      cat "${LOG_OF_PID[$i]}"
    else
      FAILURES+=("${SVC_OF_PID[$i]}")
      ecom_ci_err "--- ${SVC_OF_PID[$i]} build log (tail 40) ---"
      tail -n 40 "${LOG_OF_PID[$i]}" >&2 || true
    fi
  done
fi

if (( ${#FAILURES[@]} > 0 )); then
  ecom_ci_err "build FAILED: ${FAILURES[*]} — logs in $ECI_OUT_DIR"
  ecom_ci_summary "❌ build failed: ${FAILURES[*]}"
  exit 1
fi

count="$(grep -c . "$IMAGES_FILE" 2>/dev/null || echo 0)"
ecom_ci_ok "built $count image(s) — $PLATFORMS ($MODE)"
ecom_ci_summary "🏗️ built $count image(s) → \`$PLATFORMS\` @ tag \`$TAG\` ($MODE)"

# Manifest-list verification: proves both architectures really landed in the
# registry — a --load build can silently be single-platform, a pushed manifest
# list cannot lie to us.
if [[ "$MODE" == "push" && "${DRY_RUN:-0}" != "1" ]] && have docker; then
  for svc in "${LIST[@]}"; do
    ref="$(ecom_ci_image_ref "$svc" "$TAG" "$REGISTRY")"
    plats="$(docker buildx imagetools inspect "$ref" \
      --format '{{range .Manifests}}{{println .Platform.OS "/" .Platform.Architecture}}{{end}}' 2>/dev/null \
      | sed 's|/|/|' | tr '\n' ' ' | sed 's/ *$//')"
    if [[ -z "$plats" ]]; then
      plats="$(docker buildx imagetools inspect "$ref" 2>/dev/null | grep -i '^MediaTypes\|^Name' >/dev/null && echo 'single-image' || echo 'unknown')"
    fi
    ecom_ci_log "  $svc → $plats"
    ecom_ci_summary "📦 \`$svc\` → $plats"
    if [[ "$PLATFORMS" == *,* && "$plats" == "single-image" ]]; then
      ecom_ci_warn "$svc was requested multi-platform but the registry has a single image"
    fi
  done
fi
