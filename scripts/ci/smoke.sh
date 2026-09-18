#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/ci/smoke.sh — "does the image actually boot and answer HTTP?" gate.
#
# The cheap, high-signal check between `buildx --load` and `--push`: uvicorn
# starts, /health answers 200, /metrics exposes Prometheus text, and the
# X-Request-ID middleware works. Deliberately dependency-free — /health must
# not need Postgres/Redis/RabbitMQ (services report "degraded", never 5xx).
#
#   scripts/ci/smoke.sh --services all                  # local ecom-<svc>:local
#   scripts/ci/smoke.sh --services product --tag 1a2b3c --registry docker.io/hub
#   scripts/ci/smoke.sh --image ecom-gateway:local
#
# Flags
#   --services LIST   all | comma list                       [default all]
#   --image  REF      explicit ref; repeatable (name inferred from the tag)
#   --tag T / --registry R
#   --timeout S       seconds to wait for /health        [default 90]
#   --env  K=V        repeatable extra -e for docker run
#   --keep            leave containers running (debugging)
#
# Exit: 0 all healthy · 1 a container failed · 3 missing docker
# ---------------------------------------------------------------------------
set -uo pipefail

# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
# shellcheck source=lib/services.sh
source "$ECI_LIB_DIR/services.sh"

SERVICES="all"; IMGS=(); TAG="local"; REGISTRY=""
TIMEOUT_S=90; KEEP=0; EXTRA_ENV=(); PLATFORM_OPT=(--platform "$(ecom_ci_host_platform)")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --services)  SERVICES="${2-}"; shift 2 ;;
    --image)     IMGS+=("$2"); shift 2 ;;
    --tag)       TAG="${2-}"; shift 2 ;;
    --registry)  REGISTRY="${2-}"; shift 2 ;;
    --timeout)   TIMEOUT_S="${2-}"; shift 2 ;;
    --env)       EXTRA_ENV+=(-e "$2"); shift 2 ;;
    --keep)      KEEP=1; shift ;;
    -h|--help)   ecom_ci_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    *)           eci_die "smoke.sh: unknown argument: $1" ;;
  esac
done

ecom_ci_require docker "install Docker Engine 24+ (README §3)"
ecom_ci_require curl "health polling uses curl"

declare -A SVC_OF_REF=()
if [[ "${#IMGS[@]}" -gt 0 ]]; then
  for ref in "${IMGS[@]}"; do
    base="${ref##*/}"; name="${base%%:*}"
    svc="${name#ecom-}"
    ecom_ci_is_known_service "$svc" || ecom_ci_warn "$ref: cannot infer service name from '$name' — using port 8080"
    SVC_OF_REF["$ref"]="$svc"
  done
else
  for s in $(ecom_ci_select "$SERVICES" | tr ' ' $'\n'); do
    ref="$(ecom_ci_image_ref "$s" "$TAG" "$REGISTRY")"
    SVC_OF_REF["$ref"]="$s"
  done
fi

RUN_ID="$$"
declare -a CONTAINERS=()
FAILED=()

cleanup() {
  local c
  for c in "${CONTAINERS[@]+"${CONTAINERS[@]}"}"; do
    [[ "$KEEP" == "1" ]] || docker stop "$c" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

poll_url() { # <url> → prints http code
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || echo 000
}

echo
ecom_ci_hdr "image smoke test — ${#SVC_OF_REF[@]} image(s), ${TIMEOUT_S}s budget each"
for ref in "${!SVC_OF_REF[@]}"; do
  svc="${SVC_OF_REF[$ref]}"
  port="$(ecom_ci_port "$svc" 2>/dev/null || echo 8080)"
  cname="eci-smoke-${svc}-${RUN_ID}"
  log="$ECI_OUT_DIR/smoke-$svc.log"
  mkdir -p "$ECI_OUT_DIR"

  printf '· %-12s %s\n' "$svc" "$ref"
  # shellcheck disable=SC2086
  if ! docker run -d --rm --name "$cname" "${PLATFORM_OPT[@]}" -p "$port" \
       "${EXTRA_ENV[@]+"${EXTRA_ENV[@]}"}" "$ref" > /dev/null 2> "$log"; then
    ecom_ci_err "  $svc: docker run failed — $(tail -n 3 "$log" | tr '\n' ' ')"
    FAILED+=("$svc")
    ecom_ci_summary "❌ smoke \`$svc\`: image not runnable"
    continue
  fi
  CONTAINERS+=("$cname")

  hostport=""
  for _ in 1 2 3 4 5; do
    hostport="$(docker port "$cname" "$port" 2>/dev/null | head -n1 | sed 's/.*://')"
    [[ -n "$hostport" ]] && break
    sleep 1
  done
  if [[ -z "$hostport" ]]; then
    ecom_ci_err "  $svc: no published port (docker port returned nothing)"
    docker logs "$cname" 2>&1 | tail -n 20 >&2 || true
    FAILED+=("$svc"); ecom_ci_summary "❌ smoke \`$svc\`: no port mapping"
    continue
  fi

  base="http://127.0.0.1:$hostport"
  code=000
  waited=0
  while (( waited < TIMEOUT_S )); do
    code="$(poll_url "$base/health")"
    [[ "$code" == "200" ]] && break
    if ! docker ps -q -f "name=$cname" --format '{{.ID}}' | grep -q .; then
      ecom_ci_err "  $svc: container exited during startup"
      docker logs "$cname" 2>&1 | tail -n 25 >&2 || true
      break
    fi
    sleep 2; waited=$(( waited + 2 ))
  done

  if [[ "$code" != "200" ]]; then
    FAILED+=("$svc")
    ecom_ci_err "  $svc: /health → HTTP $code after ${waited}s"
    ecom_ci_summary "❌ smoke \`$svc\`: /health HTTP $code"
    continue
  fi

  health_json="$(curl -s --max-time 5 "$base/health")"
  metrics_code="$(poll_url "$base/metrics")"
  rid="$(curl -s -D - -o /dev/null --max-time 5 -H 'X-Request-ID: eci-smoke-1' "$base/health" \
         | tr -d '\r' | awk 'tolower($1)=="x-request-id:"{print $2}')"
  status_field="$(printf '%s' "$health_json" | python3 -c '
import json,sys
try: print(json.load(sys.stdin).get("status","?"))
except Exception: print("unparsable")
' 2>/dev/null || echo "?")"
  service_field="$(printf '%s' "$health_json" | python3 -c '
import json,sys
try: print(json.load(sys.stdin).get("service","-"))
except Exception: print("-")
' 2>/dev/null || echo "-")"

  problems=()
  [[ "$metrics_code" == "200" ]] || problems+=("/metrics HTTP $metrics_code")
  [[ "$rid" == "eci-smoke-1" ]] || problems+=("X-Request-ID not echoed (got '${rid:-none}')")
  [[ "$status_field" == "healthy" || "$status_field" == "degraded" ]] || problems+=("unexpected status '$status_field'")

  if (( ${#problems[@]} )); then
    FAILED+=("$svc")
    ecom_ci_err "  $svc: ${problems[*]}"
    ecom_ci_summary "❌ smoke \`$svc\`: ${problems[*]}"
  else
    # "degraded" is expected without Postgres/Redis present: it proves the
    # service reported its dependency state instead of 500-ing.
    ecom_ci_ok "  $svc: /health 200 ($status_field, service=$service_field) · /metrics 200 · request-id echoed"
    ecom_ci_summary "✅ smoke \`$svc\` → $status_field in ${waited}s"
  fi
  docker logs "$cname" > "$log" 2>&1 || true
  [[ "$KEEP" == "1" ]] && ecom_ci_log "  kept $cname (port $hostport, logs $log)"
done

echo
if (( ${#FAILED[@]} > 0 )); then
  ecom_ci_err "smoke test FAILED for: ${FAILED[*]}"
  exit 1
fi
ecom_ci_ok "smoke test passed for all ${#SVC_OF_REF[@]} image(s)"
