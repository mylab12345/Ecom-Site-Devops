#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/ci/unit-tests.sh — stage 2 of the Phase 2 pipeline: unit + contract
# tests, with a JUnit report Jenkins can publish.
#
# Two tiers, so the gate always runs fast and never depends on secrets:
#   structure   tests marked `not app`  — pure stdlib, repo/CI consistency
#   app         tests marked `app`      — import the 10 FastAPI apps, assert
#                                         /health, /metrics, X-Request-ID,
#                                         pydantic rules and JWT helpers
#
# Usage:
#   scripts/ci/unit-tests.sh [--tier auto|structure|app|all] [--docker] [-- <pytest args...>]
#
# Env:
#   ECOM_TEST_TIER   same as --tier (default: auto)
#   ECOM_TEST_DOCKER 1 → run inside python:3.12-slim with service deps (what Jenkins uses)
#   ECOM_PYTHON      interpreter override
#   STRICT_TOOLS     1 → missing test deps are an error, not a skip
#
# Exit codes: 0 passed · 1 failed · 3 environment problem
# ---------------------------------------------------------------------------
set -uo pipefail

# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TIER="${ECOM_TEST_TIER:-auto}"
USE_DOCKER="${ECOM_TEST_DOCKER:-0}"
QUIET=0
PYTEST_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier)   TIER="${2-}"; shift 2 ;;
    --docker) USE_DOCKER=1; shift ;;
    --quiet|-q) QUIET=1; shift ;;
    --)       shift; PYTEST_ARGS+=("$@"); break ;;
    -h|--help) ecom_ci_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    *)        PYTEST_ARGS+=("$1"); shift ;;
  esac
done

JUNIT_STRUCTURE="$(ecom_ci_out junit-structure.xml)"
JUNIT_APP="$(ecom_ci_out junit-app.xml)"

# --- docker mode: identical environment to the Jenkins agent ---------------
if [[ "$USE_DOCKER" == "1" ]]; then
  ecom_ci_require docker "install Docker Engine 24+ (README §3)"
  mkdir -p "$ECI_OUT_DIR"
  ecom_ci_log "running tests in ${ECOM_TEST_IMAGE:-python:3.12-slim} (installs requirements-dev.txt — first run ~2 min)"
  # Reports are written to the host's .ci-output dir so Jenkins can publish them.
  exec docker run --rm \
    -v "$ECOM_ROOT:/work" \
    -v "$ECI_OUT_DIR:/out" \
    -w /work \
    -e ECOM_TEST_TIER="$TIER" \
    -e ECI_OUT_DIR=/out \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e HOME=/tmp \
    "${ECOM_TEST_IMAGE:-python:3.12-slim}" \
    bash -c '
      set -o pipefail
      pip install --quiet --no-cache-dir -r /work/requirements-dev.txt || exit 3
      # Service runtime deps, so the app tier can import all 10 FastAPI apps.
      pip install --quiet --no-cache-dir $(for r in /work/services/*/requirements.txt; do printf -- "-r %s " "$r"; done) || exit 3
      bash scripts/ci/unit-tests.sh --tier "${ECOM_TEST_TIER:-auto}"'
fi

PY="${ECOM_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if have python3; then PY=python3; elif have python; then PY=python; else
    ecom_ci_require python3 "install python3.11+"
  fi
fi

run_pytest() { # <label> <marker-expr|''> <junit-file>
  local label="$1" marker="$2" junit="$3"
  local args=(-p no:cacheprovider -q --rootdir "$ECOM_ROOT" -o "junit_family=xunit1"
              "--junitxml=$junit" "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}")
  [[ -n "$marker" ]] && args+=(-m "$marker")
  ecom_ci_hdr "pytest — $label"
  mkdir -p "$(dirname "$junit")"
  if "$PY" -m pytest "$ECOM_ROOT/tests" "${args[@]}"; then
    ecom_ci_ok "$label passed"
    ecom_ci_summary "✅ pytest $label"
    return 0
  fi
  ecom_ci_err "$label FAILED"
  ecom_ci_summary "❌ pytest $label"
  return 1
}

if ! "$PY" -m pytest --version >/dev/null 2>&1; then
  ecom_ci_err "pytest is not installed for $PY"
  ecom_ci_err "  local:  pip install -r requirements-dev.txt   (or: make ci-deps)"
  ecom_ci_err "  jenkins: the pipeline installs requirements-dev.txt inside python:3.12-slim"
  exit 3
fi

RC=0
case "$TIER" in
  structure)
    run_pytest "structure" "not app" "$JUNIT_STRUCTURE" || RC=1
    ;;
  app)
    run_pytest "app contracts" "app" "$JUNIT_APP" || RC=1
    ;;
  all)
    run_pytest "structure" "not app" "$JUNIT_STRUCTURE" || RC=1
    run_pytest "app contracts" "app" "$JUNIT_APP" || RC=1
    ;;
  auto)
    run_pytest "structure" "not app" "$JUNIT_STRUCTURE" || RC=1
    if "$PY" -c 'import fastapi, httpx, pydantic_settings' 2>/dev/null; then
      run_pytest "app contracts" "app" "$JUNIT_APP" || RC=1
    elif [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
      ecom_ci_err "app tier required in strict mode but service deps are not importable"
      ecom_ci_summary "❌ pytest app tier: deps missing (strict)"
      RC=1
    else
      ecom_ci_warn "skipping app tier — service deps not installed (pip install -r requirements-dev.txt)"
      ecom_ci_summary "⚠️ app tier skipped (deps not installed)"
    fi
    ;;
  *)
    eci_die "unit-tests.sh: unknown --tier '$TIER' (auto|structure|app|all)"
    ;;
esac

exit "$RC"
