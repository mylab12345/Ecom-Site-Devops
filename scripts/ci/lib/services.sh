#!/usr/bin/env bash
# shellcheck shell=bash
# ---------------------------------------------------------------------------
# scripts/ci/lib/services.sh — single source of truth for the service registry
# used by every CI script (lint, build, scan, smoke, gitops-bump) and by the
# Jenkinsfile parallel matrix.
#
# Fields (pipe separated), in build order:
#   name | port | gateway prefix | data dependency | tier
#
# tier:  stateless  -> spot-friendly, rebuilt/scanned first
#        critical   -> money/money-movement paths, scanned with the strict gate
#
# tests/test_ci_hygiene.py asserts this table against docker-compose.yaml,
# services/gateway/app/config.py and services/*/Dockerfile, so the registry
# cannot drift silently.
# ---------------------------------------------------------------------------

[[ -n "${_ECOM_CI_SERVICES_SH:-}" ]] && return 0
_ECOM_CI_SERVICES_SH=1

ECOM_CI_SERVICES_RAW=(
  "identity|8001|/api/auth|postgres|critical"
  "product|8002|/api/products|postgres|stateless"
  "inventory|8003|/api/inventory|postgres|stateless"
  "cart|8004|/api/cart|redis|stateless"
  "order|8005|/api/orders|postgres|critical"
  "payment|8006|/api/payments|postgres|critical"
  "shipping|8007|/api/shipments|postgres|critical"
  "notification|8008|/api/notifications|rabbitmq|stateless"
  "review|8009|/api/reviews|postgres|stateless"
  "gateway|8080|/|-|edge"
)

# Docker Hub / registry image name prefix: ecom-identity, ecom-product, ...
ECOM_IMAGE_PREFIX="${ECOM_IMAGE_PREFIX:-ecom}"
export ECOM_IMAGE_PREFIX

ecom_ci_services() {
  local row
  for row in "${ECOM_CI_SERVICES_RAW[@]}"; do
    printf '%s\n' "${row%%|*}"
  done
}

_ecom_ci_field() { # <name> <field-index 1-based>
  local name="$1" idx="$2" row
  for row in "${ECOM_CI_SERVICES_RAW[@]}"; do
    if [[ "${row%%|*}" == "$name" ]]; then
      local IFS='|'
      # shellcheck disable=SC2206
      local parts=($row)
      printf '%s\n' "${parts[$((idx - 1))]}"
      return 0
    fi
  done
  return 1
}

ecom_ci_port()        { _ecom_ci_field "$1" 2; }
ecom_ci_prefix()      { _ecom_ci_field "$1" 3; }
ecom_ci_depends_on()  { _ecom_ci_field "$1" 4; }
ecom_ci_tier()        { _ecom_ci_field "$1" 5; }
ecom_ci_context()     { printf '%s/services/%s\n' "$ECOM_ROOT" "$1"; }
ecom_ci_dockerfile()  { printf '%s/Dockerfile\n' "$(ecom_ci_context "$1")"; }
ecom_ci_image_name()  { printf '%s-%s\n' "$ECOM_IMAGE_PREFIX" "$1"; }

# Qualified image reference: [registry/]image:tag
ecom_ci_image_ref() { # <name> <tag> [registry]
  local name="$1" tag="$2" registry="${3:-${REGISTRY:-}}"
  local img
  img="$(ecom_ci_image_name "$name")"
  if [[ -n "$registry" ]]; then
    printf '%s/%s:%s\n' "${registry%/}" "$img" "$tag"
  else
    printf '%s:%s\n' "$img" "$tag"
  fi
}

ecom_ci_is_known_service() {
  local name="$1" s
  for s in $(ecom_ci_services); do
    [[ "$s" == "$name" ]] && return 0
  done
  return 1
}

# Resolve "all" / "auto" / a comma-separated list into a validated, de-duplicated,
# canonically-ordered space-separated list. Dies on unknown names.
#   "auto" = only services whose sources changed (needs ECOM_CHANGED_FILES).
ecom_ci_select() {
  local spec="${1:-all}"
  local all=(); local s
  while read -r s; do all+=("$s"); done < <(ecom_ci_services)

  local want=()
  case "$spec" in
    ""|all|ALL)
      want=("${all[@]}")
      ;;
    auto|changed)
      local changed="${ECOM_CHANGED_FILES:-}"
      if [[ -z "$changed" ]]; then
        want=("${all[@]}")
      else
        for s in "${all[@]}"; do
          if printf '%s\n' "$changed" | grep -q "^services/$s/"; then
            want+=("$s")
          fi
        done
        # Shared CI code (scripts/ci, tests, Jenkinsfile) affects all services.
        if printf '%s\n' "$changed" | grep -Eq '^(scripts/ci/|tests/|Jenkinsfile|pyproject\.toml)'; then
          want=("${all[@]}")
        fi
      fi
      ;;
    *)
      # `IFS=, read` as a command prefix: a `local IFS` here would leak into the
      # validation loops below and break $(ecom_ci_services) word-splitting.
      IFS=',' read -ra want <<< "$spec"
      ;;
  esac

  # Validate + canonical order + dedupe
  local out=() bad=()
  for s in "${all[@]}"; do
    local w
    for w in "${want[@]}"; do
      w="${w// /}"
      [[ -z "$w" ]] && continue
      if [[ "$w" == "$s" ]]; then
        out+=("$s")
      fi
    done
  done
  for w in "${want[@]}"; do
    w="${w// /}"
    [[ -z "$w" ]] && continue
    ecom_ci_is_known_service "$w" || bad+=("$w")
  done
  if (( ${#bad[@]} > 0 )); then
    eci_die "unknown service(s): ${bad[*]} — known: ${all[*]}"
  fi
  (( ${#out[@]} == 0 )) && { printf '\n'; return 1; }
  ( IFS=' '; printf '%s' "${out[*]}" )
}
