#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/ci/gitops-bump.sh — the GitOps trigger (Phase 2 deliverable).
#
# Jenkins does NOT deploy anything. It rewrites `image.repository` + `image.tag`
# in helm-charts/<service>/values.yaml and commits that. ArgoCD (Phase 5)
# watches the repo and syncs within its poll interval. That keeps "what is
# running" always equal to "what is in git", and rollbacks are `git revert`.
#
#   scripts/ci/gitops-bump.sh --dry-run                       # show the diff only
#   scripts/ci/gitops-bump.sh --images-file .ci-output/images.txt
#   GITOPS_PUSH=1 GITOPS_PR=1 scripts/ci/gitops-bump.sh       # CI mode
#
# Flags / env
#   --images-file F   TSV from build.sh  [default .ci-output/images.txt]
#   --charts-dir D    [default helm-charts]
#   --dry-run         rewrite a copy under .ci-output and print the diff; the working
#                     tree is never touched, and --commit/--push are refused alongside it
#   --commit          git add + commit on the current HEAD (no push)
#   --push            commit, then push HEAD:refs/heads/$GITOPS_BRANCH
#   --open-pr         after pushing, open a PR with gh (needs GH_TOKEN)
#   GITOPS_BRANCH     target branch        [default gitops/main]
#   GITOPS_BASE       PR base branch       [default main]
#   GIT_REMOTE        remote name/url      [default origin]
#   GITOPS_RESET_AFTER_PUSH  1 (default in CI) → restore the workspace post-push
#   GITOPS_ALLOW_DIRECT_MAIN 1 → allow GITOPS_BRANCH=main (self-healing repos only)
#
# Never logs credentials: GIT_REMOTE URLs containing user:token@ are redacted.
# Exit: 0 bumped/nothing-to-do · 1 refused (malformed values) · 3 git/env problem
# ---------------------------------------------------------------------------
set -uo pipefail

# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

IMAGES_FILE="$ECI_OUT_DIR/images.txt"
CHARTS_DIR="helm-charts"
DIGEST_MODE="${GITOPS_DIGEST_MODE:-if-present}"
DO_DRY=0; DO_COMMIT=0; DO_PUSH=0; DO_PR=0
BRANCH="${GITOPS_BRANCH:-gitops/main}"
BASE="${GITOPS_BASE:-main}"
REMOTE="${GIT_REMOTE:-origin}"
REPORT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --images-file) IMAGES_FILE="${2-}"; shift 2 ;;
    --charts-dir)  CHARTS_DIR="${2-}"; shift 2 ;;
    --branch)      BRANCH="${2-}"; shift 2 ;;
    --base)        BASE="${2-}"; shift 2 ;;
    --remote)      REMOTE="${2-}"; shift 2 ;;
    --digest-mode) DIGEST_MODE="${2-}"; shift 2 ;;
    --report)      REPORT="${2-}"; shift 2 ;;
    --dry-run)     DO_DRY=1; shift ;;
    --commit)      DO_COMMIT=1; shift ;;
    --push)        DO_PUSH=1; DO_COMMIT=1; shift ;;
    --open-pr)     DO_PR=1; DO_PUSH=1; DO_COMMIT=1; shift ;;
    -h|--help)     ecom_ci_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    *)             eci_die "gitops-bump.sh: unknown argument: $1" ;;
  esac
done

if [[ "$DO_DRY" == "1" && ( "$DO_COMMIT" == "1" || "$DO_PUSH" == "1" ) ]]; then
  eci_die "--dry-run cannot be combined with --commit/--push — a flag people add to look at a diff must never be the thing that writes to git"
fi

ecom_ci_require git "git is required to commit the bump"
ecom_ci_require python3 "python3 rewrites the values block (scripts/ci/lib/bump_values.py)"
cd "$ECOM_ROOT" || eci_die "cannot cd to $ECOM_ROOT"

if [[ ! -f "$IMAGES_FILE" ]]; then
  ecom_ci_err "no image manifest at $IMAGES_FILE — run scripts/ci/build.sh first"
  ecom_ci_err "  (dry run?  printf 'product\\tmyhub/ecom-product:demo\\t-\\n' > $IMAGES_FILE )"
  exit 3
fi
git rev-parse --git-dir >/dev/null 2>&1 || eci_die "not a git repository: $ECOM_ROOT"

# Refuse to let a bot write straight to the protected default branch by accident.
if [[ "$BRANCH" == "$BASE" || "$BRANCH" == "main" || "$BRANCH" == "master" ]]; then
  if [[ "${GITOPS_ALLOW_DIRECT_MAIN:-0}" != "1" ]]; then
    ecom_ci_err "GITOPS_BRANCH=$BRANCH is the base branch — a bump bot must land on a reviewable branch."
    ecom_ci_err "  use --branch gitops/main (default), or set GITOPS_ALLOW_DIRECT_MAIN=1 for a self-healing repo."
    exit 1
  fi
  ecom_ci_warn "direct push to $BRANCH enabled (GITOPS_ALLOW_DIRECT_MAIN=1) — ArgoCD selfHeal must not fight you"
fi

ORIG_HEAD="$(git rev-parse HEAD)"
SHORT_SHA="$(git rev-parse --short=7 HEAD)"
MSG_SUBJECT="chore(gitops): bump image tags for $SHORT_SHA"
MSG="Automated image tag bump from Jenkins build #${BUILD_NUMBER:-local}.

Source commit: $ORIG_HEAD
Image manifest: $IMAGES_FILE

ArgoCD syncs these values; no deploy step runs in CI.
[skip ci]"

redact() { sed -E 's#(://)[^/@[:space:]]+@#\1***@#g'; }

# --- 1. rewrite values files ------------------------------------------------
BUMP_LOG="$ECI_OUT_DIR/gitops-bump.md"
: > "$BUMP_LOG"
[[ -n "$REPORT" ]] || REPORT="$BUMP_LOG"

run_bump() {   # $1 = charts dir to rewrite (the real one, or the dry-run copy)
  local rc=0
  python3 "$ECI_LIB_DIR/bump_values.py" \
    --images-file "$IMAGES_FILE" \
    --charts-dir "$1" \
    --digest-mode "$DIGEST_MODE" \
    --report "$REPORT" || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) ecom_ci_err "one or more values files were refused — nothing was committed"; exit 1 ;;
    *) exit 3 ;;
  esac
}

if [[ "$DO_DRY" == "1" ]]; then
  # A "dry" run must not touch the working tree — on a Jenkins agent the workspace is
  # reused, and half-written charts end up in the next build's `git status`. So bump a
  # copy under $ECI_OUT_DIR and diff it. Two mirrors (a/, b/) so the headers come out
  # as `--- a/helm-charts/x` / `+++ b/helm-charts/x`: `git apply` takes the patch as-is.
  DRY_DIR="$ECI_OUT_DIR/gitops-dry"
  rm -rf "$DRY_DIR" && mkdir -p "$DRY_DIR/a" "$DRY_DIR/b"
  cp -a "$CHARTS_DIR" "$DRY_DIR/a/$(basename "$CHARTS_DIR")" \
    && cp -a "$CHARTS_DIR" "$DRY_DIR/b/$(basename "$CHARTS_DIR")" \
    || eci_die "cannot stage the dry-run copies in $DRY_DIR"
  run_bump "$DRY_DIR/b/$(basename "$CHARTS_DIR")"

  mapfile -t DRY_CHANGED < <(diff -rq -- "$DRY_DIR/a" "$DRY_DIR/b" 2>/dev/null \
    | sed -nE "s/^Files .* and '?(.*)'? differ.*$/\1/p" | grep -E 'values\.yaml$' || true)
  echo
  if (( ${#DRY_CHANGED[@]} == 0 )); then
    ecom_ci_ok "dry-run: values files already carry these tags — nothing would be committed"
    exit 0
  fi
  # Saved as a patch, not just printed: `git apply .ci-output/gitops-bump.patch` replays
  # exactly what CI would commit, e.g. to run `helm template` over it before believing it.
  ( cd "$DRY_DIR" && diff -ru a/ b/ ) > "$ECI_OUT_DIR/gitops-bump.patch" || true
  printf -- '--- proposed diff (%d file(s)) ---\n' "${#DRY_CHANGED[@]}"
  sed -n '1,160p' "$ECI_OUT_DIR/gitops-bump.patch"
  echo
  ecom_ci_ok "dry-run complete — ${#DRY_CHANGED[@]} values file(s) would change (working tree untouched)"
  ecom_ci_log "full patch: $ECI_OUT_DIR/gitops-bump.patch"
  ecom_ci_summary "🧪 gitops dry-run: ${#DRY_CHANGED[@]} values file(s) would change"
  exit 0
fi

run_bump "$CHARTS_DIR"

# --- 2. is there anything to commit? ---------------------------------------
mapfile -t CHANGED < <(git status --porcelain -- "$CHARTS_DIR" | awk '{print $2}' | grep -E 'values\.yaml$' || true)
if [[ "${#CHANGED[@]}" -eq 0 ]]; then
  ecom_ci_ok "helm values already carry these tags — nothing to bump"
  ecom_ci_summary "♻️ gitops: no change needed ($SHORT_SHA)"
  exit 0
fi

printf '\n--- git status (%d file(s)) ---\n' "${#CHANGED[@]}"
git status --porcelain -- "$CHARTS_DIR"
printf '\n--- diff ---\n'
git --no-pager diff -- "$CHARTS_DIR" | head -n 120


# --- 3. commit ---------------------------------------------------------------
git add -- "${CHANGED[@]}" || eci_die "git add failed"
git -c user.name="${GIT_AUTHOR_NAME:-ecom-ci}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-ecom-ci@localhost}" \
    commit -q -m "$MSG_SUBJECT" -m "$MSG" || eci_die "git commit failed (hooks? signing? dirty index?)"
NEW_HEAD="$(git rev-parse HEAD)"
ecom_ci_ok "committed $NEW_HEAD ($BRANCH)"
ecom_ci_summary "📝 gitops commit \`$(git rev-parse --short=7 HEAD)\` → ${#CHANGED[@]} values file(s)"

# --- 4. push -----------------------------------------------------------------
if [[ "$DO_PUSH" == "1" ]]; then
  remote_url="$(git remote get-url "$REMOTE" 2>/dev/null || true)"
  if [[ -z "$remote_url" && -n "${GIT_REMOTE_URL:-}" ]]; then
    git remote set-url --add --push "$REMOTE" "$GIT_REMOTE_URL" 2>/dev/null \
      || git remote add "$REMOTE" "$GIT_REMOTE_URL"
    remote_url="$GIT_REMOTE_URL"
  fi
  [[ -n "$remote_url" ]] || { ecom_ci_err "remote '$REMOTE' has no URL — set GIT_REMOTE_URL or add a remote"; exit 3; }
  ecom_ci_log "pushing HEAD → $(printf '%s' "$remote_url" | redact) : refs/heads/$BRANCH"

  # Network/registry blips are what `retry` is for; a *rejected* push is different —
  # it means $BRANCH moved under us. The job runs disableConcurrentBuilds(), so the
  # usual author of that move is a build of another branch (or a human) writing the
  # same gitops branch. Rebase once and retry, because our change is a deterministic
  # rewrite of image.tag: replaying it is always safe, and a conflict on it means two
  # builds disagreed about the tag — exactly the case a human must see.
  if ! ecom_ci_retry 3 git push -q "$REMOTE" "HEAD:refs/heads/$BRANCH"; then
    if git fetch -q "$REMOTE" "$BRANCH" 2>/dev/null; then
      ecom_ci_warn "push rejected — replaying the bump on top of $REMOTE/$BRANCH"
      if git rebase -q FETCH_HEAD; then
        if ecom_ci_retry 3 git push -q "$REMOTE" "HEAD:refs/heads/$BRANCH"; then
          ecom_ci_ok "pushed refs/heads/$BRANCH (after rebase onto the newer head)"
          ecom_ci_summary "🚀 pushed \`$BRANCH\` after replaying on top of a newer bump"
          DO_PUSH_OK=1
        fi
      else
        git rebase --abort || true
        ecom_ci_err "two bumps touched the same image.tag — refusing to guess which build owns it"
      fi
    fi
    if [[ "${DO_PUSH_OK:-0}" != "1" ]]; then
      ecom_ci_err "push rejected — branch protection, missing credentials, or a stale workspace?"
      ecom_ci_err "  hint: grant the Jenkins credential 'gitops-token' write access (jenkins/setup.md §6)"
      ecom_ci_err "  manual recovery: git fetch origin $BRANCH && git rebase FETCH_HEAD && git push origin HEAD:refs/heads/$BRANCH"
      exit 1
    fi
  fi
  ecom_ci_ok "pushed refs/heads/$BRANCH"
  ecom_ci_summary "🚀 pushed \`$BRANCH\` — ArgoCD will sync within its poll interval"
fi

# --- 5. optional PR ----------------------------------------------------------
if [[ "$DO_PR" == "1" ]]; then
  if have gh; then
    pr_body="$ECI_OUT_DIR/pr-body.md"
    {
      echo "Automated image bump for \`$SHORT_SHA\`."
      echo
      cat "$REPORT"
      echo
      "ArgoCD picks these tags up once merged (Phase 5). Rollback = revert this PR."
    } > "$pr_body"
    if gh pr create --head "$BRANCH" --base "$BASE" --title "$MSG_SUBJECT" --body-file "$pr_body" 2>/dev/null; then
      ecom_ci_ok "pull request opened against $BASE"
    else
      ecom_ci_warn "gh could not open the PR (already open? GH_TOKEN missing?) — compare URL below"
    fi
  else
    ecom_ci_warn "gh CLI not installed — open the PR manually"
  fi
  repo="$(git remote get-url "$REMOTE" 2>/dev/null | sed -E 's#\.git$##; s#^git@github.com:##; s#^https://[^@]*@github.com/#https://github.com/#')"
  ecom_ci_log "compare: ${repo}/compare/${BASE}...${BRANCH}"
fi

# --- 6. leave the workspace exactly as we found it ---------------------------
# Jenkins reuses workspaces; a stray commit or modified chart would confuse the
# next build's `git rev-parse`. Reset only after a successful push.
if [[ "$DO_PUSH" == "1" && "${GITOPS_RESET_AFTER_PUSH:-1}" == "1" ]]; then
  git reset --hard -q "$ORIG_HEAD" || ecom_ci_warn "workspace reset failed (harmless if the job cleans the workspace)"
  git clean -fdq -- "$CHARTS_DIR" 2>/dev/null || true
  ecom_ci_log "workspace restored to $SHORT_SHA"
fi
