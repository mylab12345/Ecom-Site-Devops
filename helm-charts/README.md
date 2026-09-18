# helm-charts/

Phase 2 delivers the **contract** the pipeline writes into; Phase 4 delivers the
chart bodies that consume it. The split exists because CI has to be able to land a
tag without a cluster being ready to render it.

## Layout

```
helm-charts/
├── <service>/values.yaml   # 10 services — real, bumpable contract (see below)
├── ecom-common/            # Phase 4: shared library chart (labels, probes, ingress snippet)
├── ingress-nginx/          # Phase 4: ingress controller values (TLS, external-dns annotations)
└── network-policies/       # Phase 4: per-namespace egress/ingress rules
```

## The bump contract (the part CI owns)

`scripts/ci/gitops-bump.sh` rewrites exactly two keys per service and refuses to
touch anything else:

```yaml
image:
  repository: docker.io/mylab12345/ecom-product   # from ECOM_IMAGE_PREFIX
  tag: "0.0.0+phase1-placeholder"                # rewritten to <branch>-<sha>
```

Rules, enforced by `tests/test_ci_hygiene.py::test_helm_values_stub_declares_the_image_contract`:

- `image:` is a top-level mapping, keys indented **exactly two spaces** — that is the
  indentation `helm-charts/<svc>/values.yaml` uses for every block. A tab anywhere in the
  file is a hard YAML error and a blocking lint failure.
- **`tag` stays quoted.** YAML 1.1 reads an unquoted `0123` as octal and `1.10` as a float;
  Helm then hands the API server a number and the manifest is corrupt in a way nobody
  notices until a rollout fails. Every tag CI produces (`main-4f2a9c1`, `pr-64-9be3f10`) is
  alphanumeric, but the quoting rule is not negotiable.
- `service.port` / `containerPort` must equal the port in `scripts/ci/lib/services.sh` and
  the `EXPOSE` in `services/<svc>/Dockerfile`. Three files, one truth, one test.

Nothing else in the file is CI's business: `replicaCount`, probes, metrics and `config` are
what a human reviews in a PR.

## Preview without touching git

```bash
make ci-plan      # what repository/tag CI would write
make gitops-dry   # the bump, applied to a copy under .ci-output — tree untouched
```

`gitops-dry` also writes `.ci-output/gitops-bump.patch`, in `git apply` format, so the
exact commit can be replayed locally and fed through `helm template` before anyone
believes it. Both commands are read-only and safe to run anywhere, including on a
developer laptop with no registry access.

## When Phase 4 replaces this

Write the templates around these keys — do not rename them. The bump script resolves
`repository`/`tag` by *pattern* against this shape, so a chart that reorders or nests the
image block silently stops receiving new tags, and the failure shows up weeks later as
"production is still running the image from March". If Phase 4 genuinely wants a different
shape, change `bump_values()` in `scripts/ci/gitops-bump.sh` in the same PR and let
`tests/test_ci_hygiene.py` fail loudly for the other nine services.
