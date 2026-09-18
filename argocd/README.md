# argocd/ — Phase 5

Intentionally not a chart. This directory will hold one `Application` manifest per service,
and the only thing Phase 2 does with ArgoCD is decide **what it watches**.

## The hand-off

```
Jenkins (Phase 2)                        ArgoCD (Phase 5)
─────────────────                        ─────────────────
build → scan → promote                   watch this repo at rev HEAD
   │
   └─ commit to origin/gitops/<branch> ─► Application.spec.source.targetRevision
      (helm-charts/<svc>/values.yaml         └─ diff → sync → rollout
       image.repository + image.tag)
```

`scripts/ci/gitops-bump.sh` writes `image.tag` on a **dedicated branch** (`GITOPS_BRANCH`,
default `gitops/main`; pushing straight to the base branch is refused unless
`GITOPS_ALLOW_DIRECT_MAIN=1`, so a bot can never land on protected `main` by accident — see
`jenkins/setup.md` §6 for the credential that may write it). ArgoCD's `Application` points its
`targetRevision` at that branch. CI never runs `argocd app sync`, `kubectl apply`, or
`helm upgrade`. Not as a style rule: as the invariant that makes rollbacks a git operation and
keeps ArgoCD's self-heal from racing a pipeline.

## Consequences Phase 2 already accepts for Phase 5's sake

| Decision here | Why ArgoCD needs it |
|---|---|
| `latest` is written only by the `Promote images` stage, after the Trivy gate | An Application pointing at a floating tag syncs whatever was pushed last, including unscanned builds. |
| The bump commits **per green build**, and writes `image.digest` alongside the tag when the registry reports one | `argocd app diff` must be a readable audit trail, and a digest pins bytes that a mutable tag cannot. |
| Bump stage is serialized: `disableConcurrentBuilds()` + a rebase-and-retry when the push is rejected | Two writers of the same `values.yaml` means one lost tag, silently. A conflict is *surfaced*, never auto-resolved. |
| Bump runs only when `GITOPS_ENABLED` **and** images were pushed **and** the branch is trunk | A PR or tag build must not be able to move production's desired state. |
| `GITOPS_ENABLED=false` turns the stage off entirely | Phase 3/4 bring-ups need the pipeline without the write, without editing the Jenkinsfile. |

## What lands here in Phase 5

```
argocd/applications/<service>.yaml     # one per service: repoURL, targetRevision, path helm-charts/<service>
argocd/applicationset.yaml             # (preferred) the same ten objects generated from the service list
argocd/projects/ecom.yaml              # source repos + destination namespaces, so a rogue Application cannot
                                       # reach kube-system
```

The ApplicationSet generator should read the same list the pipeline does — `scripts/ci/lib/services.sh`
is the only service registry in this repo (`make ci-plan` prints it as JSON if a generator needs it).
