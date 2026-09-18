# network-policies (Phase 4)

Per-namespace `NetworkPolicy` objects, one file per edge, default-deny in.

Phase 2 fixes the topology these must match, because the smoke test in the pipeline
(`Jenkinsfile` → `E2E smoke test`) already exercises exactly these hops:

```
ingress → gateway:8080
gateway → identity:8001 product:8002 inventory:8003 cart:8004 order:8005
          payment:8006 shipping:8007 notification:8008 review:8009
product → postgres:5432   identity → postgres   order → postgres + rabbitmq
cart    → redis:6379      notification → rabbitmq:5672
```

Ports come from `scripts/ci/lib/services.sh`; if Phase 4 needs a hop the gateway does not
already make, the service code changes first and the pipeline picks it up — a policy that
allows traffic nothing sends is audit noise.

`tests/test_service_contracts.py` (app tier) proves the fan-out direction by reading the
gateway's `ROUTES` table, so the edge list above cannot silently drift from the code.
