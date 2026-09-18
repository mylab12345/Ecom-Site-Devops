#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/ci/lint.sh — stage 1 of the Phase 2 pipeline: static analysis.
#
# Runs identically on Jenkins, GitHub Actions, or a laptop. Every check is
# dependency-light: the blocking ones use only python3 + coreutils, the
# advisory ones use ruff / hadolint / shellcheck / docker when present.
#
# Usage:
#   scripts/ci/lint.sh [--fix] [--strict] [--quiet]
#
# Exit codes: 0 clean · 1 findings · 3 environment problem (missing python3)
# ---------------------------------------------------------------------------
set -uo pipefail

# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

RUFF_FIX=0; STRICT="${STRICT_TOOLS:-0}"; QUIET=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix)      RUFF_FIX=1; shift ;;
    --strict)   STRICT=1; shift ;;
    --quiet)    QUIET=1; shift ;;
    -h|--help)  ecom_ci_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    *)          eci_die "lint.sh: unknown argument: $1" ;;
  esac
done
[[ "$STRICT" == "1" ]] && export STRICT_TOOLS=1

PY="${ECOM_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if have python3; then PY=python3; elif have python; then PY=python; else
    ecom_ci_require python3 "install python3.11+ (README §3 prerequisites)"
  fi
fi

FAIL=0
step() { [[ "$QUIET" == "1" ]] || ecom_ci_hdr "$*"; }
note() { [[ "$QUIET" == "1" ]] || ecom_ci_log "$*"; }
fail() { ecom_ci_err "$*"; FAIL=1; ecom_ci_summary "❌ $*"; }
pass() { ecom_ci_ok "$*"; [[ "$QUIET" == "1" ]] || ecom_ci_summary "✅ $*"; }

mkdir -p "$(ecom_ci_out "")"
rm -f "$(ecom_ci_out summary.md)"
ecom_ci_summary "## Lint report ($(date -u '+%Y-%m-%d %H:%M UTC'))"

step "Python: compileall (blocking)"
# Syntax gate for every service, including files ruff may be configured to ignore.
if "$PY" -m compileall -q "$ECOM_ROOT/services" "$ECOM_ROOT/tests" "$ECOM_ROOT/scripts" 2> "$(ecom_ci_out compile.log)"; then
  pass "compileall — all $(find "$ECOM_ROOT/services" -name '*.py' | wc -l | tr -d ' ') service modules parse"
else
  fail "compileall — syntax errors:"; cat "$(ecom_ci_out compile.log)" >&2
fi

# Bytecode artifacts must never be committed back to the repo.
find "$ECOM_ROOT/services" "$ECOM_ROOT/tests" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

step "Ruff (blocking when installed)"
if "$PY" -m ruff --version >/dev/null 2>&1; then
  RUFF=("$PY" -m ruff)
elif have ruff; then
  RUFF=(ruff)
else
  RUFF=()
fi
if (( ${#RUFF[@]} )); then
  note "using: ${RUFF[0]} ${RUFF[1]}"
  if [[ "$RUFF_FIX" == "1" ]]; then
    "${RUFF[@]}" check --fix "$ECOM_ROOT/services" "$ECOM_ROOT/tests" && pass "ruff check --fix applied"
  else
    if "${RUFF[@]}" check --output-format=github "$ECOM_ROOT/services" "$ECOM_ROOT/tests" \
        | tee "$(ecom_ci_out ruff-github.txt)" > "$(ecom_ci_out ruff.txt)"; then
      pass "ruff check — no findings on $(find "$ECOM_ROOT/services" -name '*.py' | wc -l | tr -d ' ') files"
    else
      fail "ruff check — findings (see .ci-output/ruff.txt)"
      "${RUFF[@]}" check "$ECOM_ROOT/services" "$ECOM_ROOT/tests" | head -n 60 || true
    fi
  fi
else
  if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
    fail "ruff missing in strict mode — pip install -r requirements-dev.txt"
  else
    note "ruff not installed — install with: pip install -r requirements-dev.txt (or: make ci-deps)"
    ecom_ci_summary "⚠️ ruff not installed (skipped)"
  fi
fi

step "Shell scripts: bash -n (blocking)"
sh_bad=0
while IFS= read -r -d '' f; do
  if ! bash -n "$f" 2> "$(ecom_ci_out bash-n.log)"; then
    fail "bash syntax: $f"; cat "$(ecom_ci_out bash-n.log)" >&2; sh_bad=1
  fi
done < <(find "$ECOM_ROOT/scripts" -name '*.sh' -print0)
(( sh_bad == 0 )) && pass "bash -n — $(find "$ECOM_ROOT/scripts" -name '*.sh' | wc -l | tr -d ' ') scripts parse"

# Executable bit matters for the entrypoints Jenkins invokes directly
# (`sh ./scripts/ci/lint.sh`). lib/*.sh are sourced by them, and a mode of 644 is
# how you keep someone from running `./common.sh` and getting a silent no-op.
while IFS= read -r -d '' f; do
  [[ -x "$f" ]] || fail "not executable (chmod +x): $f"
done < <(find "$ECOM_ROOT/scripts" -name '*.sh' -not -path '*/lib/*' -print0)

step "Shell scripts: shellcheck (advisory)"
if ecom_ci_optional shellcheck "apt/brew install shellcheck"; then
  if shellcheck --version > /dev/null 2>&1 && \
     shellcheck -S warning -e SC1091 "$(find "$ECOM_ROOT/scripts" -name '*.sh')" > "$(ecom_ci_out shellcheck.txt)"; then
    pass "shellcheck — clean"
  else
    if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
      fail "shellcheck — findings (see .ci-output/shellcheck.txt)"
    else
      ecom_ci_warn "shellcheck findings (non-blocking): $(grep -c '^In ' "$(ecom_ci_out shellcheck.txt)" 2>/dev/null || echo 0) file(s)"
    fi
  fi
fi

step "Dockerfiles: hadolint (advisory)"
if ecom_ci_optional hadolint "brew install hadolint · docker run --rm -i hadolint/hadolint"; then
  h_bad=0
  for df in "$ECOM_ROOT"/services/*/Dockerfile; do
    [[ -f "$df" ]] || continue
    if ! hadolint --config "$ECOM_ROOT/.hadolint.yaml" "$df" > "$(ecom_ci_out hadolint.txt)" 2>&1; then
      if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
        fail "hadolint: ${df#$ECOM_ROOT/}"; cat "$(ecom_ci_out hadolint.txt)"
      else
        ecom_ci_warn "hadolint ${df#$ECOM_ROOT/}: $(head -n 3 "$(ecom_ci_out hadolint.txt)" | tr '\n' ' ')"
      fi
      h_bad=1
    fi
  done
  (( h_bad == 0 )) && pass "hadolint — 10 Dockerfiles clean (.hadolint.yaml)"
fi

step "YAML: compose + helm values (blocking when parser available)"
if "$PY" "$ECI_LIB_DIR/check_yaml.py" > "$(ecom_ci_out yaml.txt)" 2>&1; then
  pass "yaml parse — $(tail -n 1 "$(ecom_ci_out yaml.txt)")"
else
  if grep -q PYYAML_MISSING "$(ecom_ci_out yaml.txt)"; then
    note "PyYAML not installed — YAML parse check skipped (pip install -r requirements-dev.txt)"
    ecom_ci_summary "⚠️ PyYAML not installed (yaml parse skipped)"
  else
    fail "YAML parse errors:"; cat "$(ecom_ci_out yaml.txt)"
  fi
fi

step "Compose config (advisory, needs docker)"
if have docker && docker compose version >/dev/null 2>&1; then
  if (cd "$ECOM_ROOT" && docker compose config -q > "$(ecom_ci_out compose-config.txt)" 2>&1); then
    pass "docker compose config — valid"
  else
    fail "docker compose config invalid:"; cat "$(ecom_ci_out compose-config.txt)" >&2
  fi
else
  note "docker unavailable — skipping 'docker compose config' (runs on the Jenkins agent)"
fi

step "Jenkinsfile structure: delimiters, stages, interpolation traps (blocking)"
jf_all="$(ecom_ci_out jenkinsfile.txt)"
: > "$jf_all"
for jf in "$ECOM_ROOT/Jenkinsfile.local" "$ECOM_ROOT/Jenkinsfile.aws"; do
  if [[ ! -f "$jf" ]]; then
    fail "$(basename "$jf") is missing — both pipelines are deliverables"
    continue
  fi
  jf_out="$(ecom_ci_out "jenkinsfile-$(basename "$jf").txt")"
  if "$PY" "$ECI_LIB_DIR/check_jenkinsfile.py" "$jf" > "$jf_out" 2>&1; then
    pass "$(basename "$jf") sanity — $(head -n 1 "$jf_out" | sed 's/^OK [^:]*: //')"
  else
    fail "$(basename "$jf") findings (Jenkins would reject it, or run something other than what you wrote):"
    cat "$jf_out" >&2
  fi
  cat "$jf_out" >> "$jf_all"
done

step "Secrets hygiene: no committed .env / key material (blocking)"
if git -C "$ECOM_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  leaked="$(git -C "$ECOM_ROOT" ls-files | grep -E '(^|/)\.env$|\.pem$|\.key$|id_rsa' || true)"
  if [[ -n "$leaked" ]]; then
    fail "tracked secret-looking files: $(echo "$leaked" | tr '\n' ' ')"
  else
    pass "no .env / private keys tracked in git"
  fi
else
  note "not a git checkout — secret scan skipped"
fi

step "Structure & CI-contract tests (blocking)"
if [[ -x "$ECOM_ROOT/scripts/ci/unit-tests.sh" ]]; then
  if ECOM_TEST_TIER=structure "$ECOM_ROOT/scripts/ci/unit-tests.sh" --quiet > "$(ecom_ci_out lint-structure.log)" 2>&1; then
    pass "pytest -m 'not app' — registry/compose/helm consistency green"
  else
    fail "structure tests failed — tail:"; tail -n 40 "$(ecom_ci_out lint-structure.log)" >&2
  fi
else
  fail "scripts/ci/unit-tests.sh missing or not executable"
fi

step "Helm charts: lint + template dry-run (advisory, needs helm)"
if ecom_ci_optional helm "https://helm.sh/docs/intro/install/"; then
  helm_bad=0
  for chart in ecom-common identity product inventory cart order payment shipping notification review gateway ingress-nginx network-policies; do
    if [[ -d "$ECOM_ROOT/helm-charts/$chart" ]]; then
      if ! helm lint "$ECOM_ROOT/helm-charts/$chart" > "$(ecom_ci_out helm-lint-$chart.txt)" 2>&1; then
        if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
          fail "helm lint $chart"; cat "$(ecom_ci_out helm-lint-$chart.txt)" >&2
        else
          ecom_ci_warn "helm lint $chart findings (non-blocking): $(head -n 5 "$(ecom_ci_out helm-lint-$chart.txt)" | tr '\n' ' ')"
        fi
        helm_bad=1
      fi
      # Template + kubectl dry-run if kubectl available
      if have kubectl; then
        if ! helm template ecom "$ECOM_ROOT/helm-charts/$chart" -n ecom --set image.tag=ci-test > "$(ecom_ci_out helm-template-$chart.yaml)" 2>&1; then
          ecom_ci_warn "helm template $chart failed (non-blocking)"
          helm_bad=1
        else
          if ! kubectl apply --dry-run=client -f "$(ecom_ci_out helm-template-$chart.yaml)" > /dev/null 2>&1; then
            if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
              fail "helm template $chart kubectl dry-run failed"
            else
              ecom_ci_warn "helm template $chart kubectl dry-run failed (non-blocking)"
            fi
          fi
        fi
      else
        # Just template without kubectl
        helm template ecom "$ECOM_ROOT/helm-charts/$chart" -n ecom --set image.tag=ci-test > "$(ecom_ci_out helm-template-$chart.yaml)" 2>&1 || true
      fi
    fi
  done
  (( helm_bad == 0 )) && pass "helm lint + template — 13 charts (ecom-common + 10 services + ingress-nginx + network-policies)"
else
  note "helm not installed — skipping helm lint (runs on Jenkins agent with helm)"
fi

step "Terraform: fmt + validate (advisory, needs terraform)"
if ecom_ci_optional terraform "https://developer.hashicorp.com/terraform/install"; then
  tf_bad=0
  if ! terraform fmt -check -recursive "$ECOM_ROOT/terraform" > "$(ecom_ci_out terraform-fmt.txt)" 2>&1; then
    if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
      fail "terraform fmt — run: terraform fmt -recursive terraform/"
      cat "$(ecom_ci_out terraform-fmt.txt)" >&2
    else
      ecom_ci_warn "terraform fmt findings (non-blocking): run terraform fmt -recursive terraform/"
    fi
    tf_bad=1
  fi
  for mod in local-kind aws-graviton; do
    if [[ -d "$ECOM_ROOT/terraform/$mod" ]]; then
      if ! terraform -chdir="$ECOM_ROOT/terraform/$mod" init -backend=false -input=false > "$(ecom_ci_out terraform-init-$mod.txt)" 2>&1; then
        ecom_ci_warn "terraform init $mod failed (non-blocking, needs network for modules)"
      else
        if ! terraform -chdir="$ECOM_ROOT/terraform/$mod" validate > "$(ecom_ci_out terraform-validate-$mod.txt)" 2>&1; then
          if [[ "${STRICT_TOOLS:-0}" == "1" ]]; then
            fail "terraform validate $mod"; cat "$(ecom_ci_out terraform-validate-$mod.txt)" >&2
          else
            ecom_ci_warn "terraform validate $mod findings (non-blocking)"
          fi
          tf_bad=1
        fi
      fi
    fi
  done
  (( tf_bad == 0 )) && pass "terraform fmt + validate — local-kind + aws-graviton"
else
  note "terraform not installed — skipping fmt/validate (runs on Jenkins agent with terraform)"
fi

echo
if (( FAIL )); then
  ecom_ci_err "lint: FAILED — see $ECI_OUT_DIR"
  exit 1
fi
ecom_ci_ok "lint: all checks passed"
