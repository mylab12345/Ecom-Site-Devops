#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/ci/scan.sh — Trivy vulnerability gate (Phase 2 deliverable).
#
# Scans the images build.sh pushed (registry-side: no pull needed), or the
# source tree (--fs), writes JSON + SARIF, renders JUnit + a markdown summary,
# and applies an *explicit* pass/fail policy instead of hiding it in tool
# defaults. A missing trivy binary is an error (exit 3), never a silent pass.
#
#   scripts/ci/scan.sh --services all --tag "$SHA" --registry docker.io/myhub
#   scripts/ci/scan.sh --image ecom-product:dev --mode report
#   scripts/ci/scan.sh --fs                        # deps + Dockerfile/compose misconfig
#   scripts/ci/scan.sh --download-db-only          # warm the DB before parallel scans
#
# Flags
#   --severity LIST      default HIGH,CRITICAL                  ($TRIVY_SEVERITY)
#   --mode gate|report   gate = fail on findings; report = always 0
#   --ignore-unfixed     on by default               ($TRIVY_IGNORE_UNFIXED)
#   --services LIST --tag T --registry R   derive refs from the CI registry
#   --image REF          repeatable; scan exactly these refs
#   --fs                 also scan the repo
#   --timeout D          trivy --timeout                              [default 10m]
#   --out-dir DIR        where reports go   [default .ci-output/trivy]
#                        (Jenkins passes one dir per service so parallel
#                         branches cannot overwrite each other's JUnit file)
#
# Output  .ci-output/trivy/<ref>.{json,sarif,txt}   .ci-output/junit-trivy.xml
#         .ci-output/trivy-summary.{md,json}        .ci-output/decision
# Exit:   0 clean/report · 1 gate findings · 3 missing tool/env
# ---------------------------------------------------------------------------
set -uo pipefail

# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
# shellcheck source=lib/services.sh
source "$ECI_LIB_DIR/services.sh"

SEVERITY="${TRIVY_SEVERITY:-HIGH,CRITICAL}"
MODE="${TRIVY_MODE:-gate}"
IGNORE_UNFIXED="${TRIVY_IGNORE_UNFIXED:-1}"
TIMEOUT="${TRIVY_TIMEOUT:-10m}"
IMAGES=(); SERVICES=""; FS_SCAN=0; DB_ONLY=0; QUIET=0
TAG="${IMAGE_TAG:-}"; REGISTRY="${REGISTRY:-}"
OUT_DIR="${ECI_OUT_DIR}/trivy"
FAILED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --severity)          SEVERITY="${2-}"; shift 2 ;;
    --mode)              MODE="${2-}"; shift 2 ;;
    --ignore-unfixed)    IGNORE_UNFIXED=1; shift ;;
    --no-ignore-unfixed) IGNORE_UNFIXED=0; shift ;;
    --services)          SERVICES="${2-}"; shift 2 ;;
    --image)             IMAGES+=("$2"); shift 2 ;;
    --tag)               TAG="${2-}"; shift 2 ;;
    --registry)          REGISTRY="${2-}"; shift 2 ;;
    --timeout)           TIMEOUT="${2-}"; shift 2 ;;
    --out-dir)           OUT_DIR="${2-}"; shift 2 ;;
    --fs)                FS_SCAN=1; shift ;;
    --download-db-only)  DB_ONLY=1; shift ;;
    --quiet|-q)          QUIET=1; shift ;;
    -h|--help)           ecom_ci_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    *)                   eci_die "scan.sh: unknown argument: $1" ;;
  esac
done

OUT="$OUT_DIR"; mkdir -p "$OUT"
ROWS="$OUT/rows.txt"
HELPER="$ECI_LIB_DIR/trivy_report.py"

ecom_ci_require python3 "python3 renders the JUnit report and the gate decision"
[[ -f "$HELPER" ]] || ecom_ci_err "missing helper $HELPER"

case "$MODE" in
  gate|report) ;;
  *) eci_die "scan.sh: --mode must be gate|report (got '$MODE')" ;;
esac

if ! ecom_ci_optional trivy "https://trivy.dev/latest/getting-started/installation/ — on Jenkins preinstall it or run aquasec/trivy (jenkins/setup.md §5)"; then
  # A security gate that silently disappears is worse than no gate at all.
  ecom_ci_err "trivy not installed — refusing to report a pass"
  ecom_ci_summary "❌ trivy missing — security gate could not run"
  exit 3
fi

TRIVY_ARGS=(--severity "$SEVERITY" --timeout "$TIMEOUT" --parallel 1)
[[ -f "$ECOM_ROOT/.trivyignore" ]] && TRIVY_ARGS+=(--ignorefile "$ECOM_ROOT/.trivyignore")
[[ "$IGNORE_UNFIXED" == "1" ]] && TRIVY_ARGS+=(--ignore-unfixed)
export ECI_SEVERITY="$SEVERITY"

# --- DB warm-up -------------------------------------------------------------
# Ten parallel Jenkins branches each downloading the ~60 MB vulnerability DB is
# a race and a bandwidth waste: warm once, then every branch runs --skip-db-update.
if (( DB_ONLY )); then
  ecom_ci_log "warming the Trivy vulnerability DB"
  trivy image --download-db-only --skip-java-db-update || {
    ecom_ci_err "Trivy DB download failed — check egress to ghcr.io"
    exit 3
  }
  ecom_ci_ok "Trivy DB ready"
  exit 0
fi

# --- targets ----------------------------------------------------------------
if [[ -z "$SERVICES" && "${#IMAGES[@]}" -eq 0 && "$FS_SCAN" == "0" ]]; then
  SERVICES="all"
fi
if [[ "$FS_SCAN" == "0" || -n "$SERVICES" || "${#IMAGES[@]}" -gt 0 ]]; then
  [[ -n "$TAG" ]] || TAG="$(git -C "$ECOM_ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo dev)"
fi
if [[ -n "$SERVICES" ]]; then
  for s in $(ecom_ci_select "$SERVICES" | tr ' ' $'\n'); do
    IMAGES+=("$(ecom_ci_image_ref "$s" "$TAG" "$REGISTRY")")
  done
fi
if [[ "$FS_SCAN" == "0" && "${#IMAGES[@]}" -eq 0 ]]; then
  ecom_ci_err "scan.sh: nothing to scan (pass --services/--image/--fs)"
  exit 3
fi

: > "$ROWS"

safe_name() { printf '%s' "$1" | tr '/:@ ' '____'; }

# --- scan a registry image --------------------------------------------------
scan_image() { # <ref>
  local ref="$1"
  local safe json
  safe="$(safe_name "$ref")"
  json="$OUT/$safe.json"
  ecom_ci_log "scanning $ref"
  if ! trivy image "${TRIVY_ARGS[@]}" --format json --output "$json" "$ref"; then
    if [[ ! -s "$json" ]]; then
      ecom_ci_err "trivy failed on $ref without producing a report (registry auth? image not pushed?)"
      printf '%s|scan-error|0|0|0\n' "$ref" >> "$ROWS"
      return 2
    fi
    ecom_ci_warn "trivy exited non-zero on $ref but produced a report — gating on it"
  fi
  trivy image "${TRIVY_ARGS[@]}" --format sarif --output "$OUT/$safe.sarif" "$ref" >/dev/null 2>&1 \
    || ecom_ci_warn "SARIF skipped for $ref (optional)"
  trivy image "${TRIVY_ARGS[@]}" --format table "$ref" > "$OUT/$safe.txt" 2>&1 || true
  [[ "$QUIET" == "1" ]] || cat "$OUT/$safe.txt"
  python3 "$HELPER" count "$ref" "$json" >> "$ROWS"
}

for img in ${IMAGES[@]+"${IMAGES[@]}"}; do
  scan_image "$img" || FAILED=1
done

# --- scan the tree (dependency + IaC misconfig) -----------------------------
if (( FS_SCAN )); then
  target="${ECOM_SCAN_PATH:-$ECOM_ROOT}"
  ecom_ci_log "filesystem scan: dependencies + IaC misconfig in ${target#"$ECOM_ROOT"/}"
  fjson="$OUT/filesystem.json"
  if ! trivy fs "${TRIVY_ARGS[@]}" --scanners vuln,misconfig --format json --output "$fjson" "$target"; then
    [[ -s "$fjson" ]] || { ecom_ci_err "trivy fs failed to produce a report"; FAILED=1; }
  fi
  if [[ -s "$fjson" ]]; then
    python3 "$HELPER" count "filesystem" "$fjson" >> "$ROWS"
  fi
  trivy fs "${TRIVY_ARGS[@]}" --scanners vuln,misconfig --format table "$target" > "$OUT/filesystem.txt" 2>&1 || true
fi

# --- JUnit + markdown + decision -------------------------------------------
report="$(python3 "$HELPER" report "$ROWS" "$OUT" "$MODE")"
decision="$(tr -d '[:space:]' < "$OUT/decision" 2>/dev/null || echo UNKNOWN)"
echo

case "$decision" in
  FAIL)
    ecom_ci_err "SECURITY GATE FAILED — $report"
    ecom_ci_err "  report: $OUT (junit-trivy.xml is published by Jenkins)"
    ecom_ci_summary "❌ trivy gate blocked — $report"
    exit 1
    ;;
  WARN)
    ecom_ci_warn "trivy found issues but mode=report — build continues. $report"
    ecom_ci_summary "⚠️ trivy report-only — $report"
    ;;
  PASS)
    ecom_ci_ok "trivy clean — $report"
    ecom_ci_summary "🛡️ trivy clean at $SEVERITY (mode \`$MODE\`)"
    ;;
  *)
    ecom_ci_err "trivy summary missing — scan did not complete"
    exit 3
    ;;
esac

if (( FAILED )); then
  ecom_ci_err "one or more trivy invocations failed (see $OUT)"
  exit 3
fi
