# ingress-nginx (Phase 4)

Values for the ingress-nginx chart, pinned by `terraform/eks/` (Phase 3) at the version that
chart dependency declares. Kept here rather than only in Terraform so the annotations the
services reference are reviewable next to them.

What Phase 2 already constrains:

- The gateway is the only HTTP entrypoint, listening on `8080`
  (`helm-charts/gateway/values.yaml`); every other service is `ClusterIP`. The gateway strips
  the leading `/api` and forwards the rest, so `/api/products/health` reaches the product
  service as `/products/health` — ingress above it must not rewrite paths, or the two layers
  fight.
- ARM64 is the default node architecture (`scheduling.architecture` in each values stub), so
  this must use the multi-arch ingress-nginx image — do not pin an amd64-only digest.

Deliberately empty until Phase 4: an ingress controller config that only exists in a chart
and never in a cluster test is untested twice over.
