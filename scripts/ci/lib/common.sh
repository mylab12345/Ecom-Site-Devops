#!/usr/bin/env bash
# shellcheck shell=bash
# ---------------------------------------------------------------------------
# scripts/ci/lib/common.sh — shared helpers for the Phase 2 CI scripts.
#
# Sourced (never executed) by every script under scripts/ci/.
#
# Public API
#   ecom_ci_root              absolute path to the repository root
#   ecom_ci_out <rel>         path under the CI output dir ($CI_OUTPUT_DIR)
#   have <cmd>                exit 0 when <cmd> is on PATH
#   ecom_ci_require <cmd> <hint>  die (hard) when <cmd> is missing
#   ecom_ci_optional <cmd> <hint> warn/die per STRICT_TOOLS, exit code for callers
#   ecom_ci_log|ok|warn|err   timestamped, colour-aware logging
#   ecom_ci_run <cmd...>      echo + execute (honours DRY_RUN=1)
#   ecom_ci_is_ci             exit 0 when a CI system is detected
#   ecom_ci_usage <script>      print the usage block from a script header
#   ecom_ci_summary <line>    append a line to the markdown build summary
#   ecom_ci_kv <file> <k> <v> idempotent "key=value" upsert in a env-style file
#   ecom_ci_host_platform     linux/amd64 | linux/arm64 for the current host
#   ecom_ci_retry <n> <cmd..> retry a flaky command (push/registry) n times
# ---------------------------------------------------------------------------

# Guard against double-sourcing.
[[ -n "${_ECOM_CI_COMMON_SH:-}" ]] && return 0
_ECOM_CI_COMMON_SH=1

ecom_ci_root() {
  # scripts/ci/lib/common.sh -> repo root is three levels up from lib/
  local src="${BASH_SOURCE[0]}"
  cd "$(dirname "$src")/../../.." && pwd -P
}

ECOM_ROOT="${ECOM_ROOT:-$(ecom_ci_root)}"
export ECOM_ROOT

ECI_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ECI_LIB_DIR

# All generated artifacts land here (git-ignored). Jenkins archives it.
ECI_OUT_DIR="${ECI_OUT_DIR:-$ECOM_ROOT/.ci-output}"
export ECI_OUT_DIR

# --- colour / verbosity -----------------------------------------------------
if [[ -t 2 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
  ECI_C_RED=$'\033[0;31m'; ECI_C_GRN=$'\033[0;32m'; ECI_C_YLW=$'\033[0;33m'
  ECI_C_BLU=$'\033[0;34m'; ECI_C_DIM=$'\033[2m';     ECI_C_OFF=$'\033[0m'
else
  ECI_C_RED=""; ECI_C_GRN=""; ECI_C_YLW=""; ECI_C_BLU=""; ECI_C_DIM=""; ECI_C_OFF=""
fi

_eci_ts() { date -u '+%H:%M:%S'; }

ecom_ci_log() { printf '%s[%s]%s %s\n' "$ECI_C_DIM" "$(_eci_ts)" "$ECI_C_OFF" "$*"; }
ecom_ci_ok()  { printf '%s[%s] ✓%s %s\n' "$ECI_C_GRN" "$(_eci_ts)" "$ECI_C_OFF" "$*"; }
ecom_ci_warn(){ printf '%s[%s] !%s %s\n' "$ECI_C_YLW" "$(_eci_ts)" "$ECI_C_OFF" "$*" >&2; }
ecom_ci_err() { printf '%s[%s] ✗%s %s\n' "$ECI_C_RED" "$(_eci_ts)" "$ECI_C_OFF" "$*" >&2; }
ecom_ci_hdr() {
  printf '\n%s%s%s\n' "$ECI_C_BLU" "─── $* ───" "$ECI_C_OFF"
}

eci_die() { ecom_ci_err "$*"; exit 1; }

# --- capability probes ------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

ecom_ci_is_ci() {
  [[ -n "${CI:-}" || -n "${JENKINS_URL:-}" || -n "${BUILD_NUMBER:-}" ]]
}

# Tool is mandatory: used for docker/buildx/git where there is no fallback.
ecom_ci_require() {
  local cmd="$1" hint="${2:-}"
  have "$cmd" && return 0
  ecom_ci_err "required tool not found: $cmd"
  [[ -n "$hint" ]] && ecom_ci_err "  fix: $hint"
  exit 3
}

# Tool is nice-to-have (trivy, hadolint, yq...). In strict mode (CI) a missing
# tool is an error so a gate can never silently become a no-op.
ecom_ci_optional() {
  local cmd="$1" hint="${2:-}"
  have "$cmd" && return 0
  if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
    ecom_ci_err "missing required tool (STRICT_TOOLS=1): $cmd"
    [[ -n "$hint" ]] && ecom_ci_err "  fix: $hint"
    return 3
  fi
  [[ -n "$hint" ]] && ecom_ci_warn "skipping: $cmd not installed — $hint" \
                   || ecom_ci_warn "skipping: $cmd not installed"
  return 1
}

# --- execution --------------------------------------------------------------
ecom_ci_run() {
  printf '%s[%s]\$%s %s\n' "$ECI_C_DIM" "$(_eci_ts)" "$ECI_C_OFF" "$*"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    return 0
  fi
  "$@"
}

ecom_ci_retry() {
  local n="$1"; shift
  local i=1
  until "$@"; do
    if (( i >= n )); then
      ecom_ci_err "command failed after $n attempts: $*"
      return 1
    fi
    ecom_ci_warn "attempt $i/$n failed, retrying in $(( i * 5 ))s: $*"
    sleep $(( i * 5 ))
    i=$(( i + 1 ))
  done
}

# --- artifacts --------------------------------------------------------------
ecom_ci_out() {
  local rel="${1:-}"
  mkdir -p "$ECI_OUT_DIR/$(dirname "$rel")" 2>/dev/null || mkdir -p "$ECI_OUT_DIR"
  printf '%s/%s' "$ECI_OUT_DIR" "$rel"
}

ecom_ci_summary() {
  local file
  file="$(ecom_ci_out summary.md)"
  printf -- '- %s\n' "$*" >> "$file"
}

# Set a key=value line in an env-style file, replacing any existing value.
ecom_ci_kv() {
  local file="$1" key="$2" value="$3"
  mkdir -p "$(dirname "$file")"
  touch "$file"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    # Escape sed replacement hazards: & and |
    local esc="${value//&/\\&}"
    sed -i.bak "s|^${key}=.*|${key}=${esc}|" "$file" && rm -f "${file}.bak"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# Print the usage block from a script's header comment (between the two
# `# ---` rule lines). Keeps --help honest without hardcoded line numbers.
ecom_ci_usage() {
  awk 'NR==1 { next }
       /^# ---/ { n++; next }
       n >= 2 { exit }
       /^#/ { sub(/^# ?/, ""); print }' "$1"
}

# --- platform helpers -------------------------------------------------------
ecom_ci_host_platform() {
  case "$(uname -m)" in
    x86_64|amd64) echo "linux/amd64" ;;
    aarch64|arm64) echo "linux/arm64" ;;
    armv7l|armhf) echo "linux/arm/v7" ;;
    ppc64le) echo "linux/ppc64le" ;;
    s390x) echo "linux/s390x" ;;
    *) eci_die "unsupported host architecture: $(uname -m)" ;;
  esac
}

# 1 when the host cannot natively run $1 (used to decide if QEMU/binfmt is needed).
ecom_ci_needs_binfmt() {
  local target="$1" host
  host="$(ecom_ci_host_platform)"
  [[ "$host" != "$target" ]]
}
