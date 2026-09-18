# Troubleshooting — Microservice Connectivity & ARM64

## 1. Quick Diagnostics

```bash
make health                 # gateway aggregated health (checks all 9)
docker compose ps           # check Up (healthy) vs restarting
docker compose logs -f <svc> --tail=100
docker compose logs postgres | head -n 100
curl -v http://localhost:8080/health | jq
curl http://localhost:8001/health  # direct
curl http://localhost:8002/health
for p in 8001 8002 8003 8004 8005 8006 8007 8008 8009 8080; do echo "=== $p ==="; curl -s http://localhost:$p/health; echo; done
docker network inspect ecom-network | grep -A 2 '"Name"'
```

---

## 2. Microservice Connectivity (Kubernetes DNS → Docker Compose DNS)

### Symptom: Gateway 502 / `Bad Gateway to product` / `Name does not resolve`

**Root cause:** service name mismatch or downstream not `healthy`.

**Fix:**

1. Confirm service name equals Compose service & env var:
   ```bash
   grep -R "PRODUCT_SERVICE_URL" services/
   # should be http://product:8002  (service key = product)
   ```
   All services use `http://<compose-name>:<port>`:
   - identity: `http://identity:8001`
   - product: `http://product:8002`
   - inventory: `http://inventory:8003`
   - cart: `http://cart:8004`
   - order: `http://order:8005`
   - payment: `http://payment:8006`
   - shipping: `http://shipping:8007`
   - notification: `http://notification:8008`
   - review: `http://review:8009`

2. In K8s, FQDN is `http://product.ecom.svc.cluster.local:8002` but short `http://product:8002` works within same namespace (`ecom`). Helm sets same env.

3. Check gateway logs:
   ```bash
   docker compose logs gateway | grep -i "proxy.*failed\|502"
   ```

4. Test DNS from inside gateway:
   ```bash
   docker exec ecom-gateway getent hosts product
   docker exec ecom-gateway getent hosts inventory
   docker exec ecom-gateway curl -v http://product:8002/health
   docker exec ecom-gateway curl -v http://inventory:8003/health
   docker exec ecom-order curl -v http://product:8002/products/1
   ```

5. If one svc is `unhealthy`, gateway returns 502. Fix that svc first:
   ```bash
   docker compose logs <svc>
   docker exec ecom-<svc> curl -f http://localhost:<port>/health || echo "self unhealthy"
   ```

### Symptom: Order 409 `Insufficient stock` or 404 `Product not found`

- Inventory is separate DB seeded with 100+ per product. If you queried before seed finished, it was empty.
- Wait 10s after `make up`, then `curl http://localhost:8080/api/inventory/1 | jq` — should show `available`.
- Adjust manually:
  ```bash
  curl -X POST http://localhost:8080/api/inventory/1/adjust -H "Content-Type: application/json" -d '{"delta":200,"reason":"restock"}'
  curl -X POST http://localhost:8080/api/inventory/2/adjust -d '{"delta":100}'
  ```
- Product not found: `curl http://localhost:8080/api/products | jq` to see IDs 1-8.

### Symptom: Order hangs / inventory reserve timeout

- Order service does 3 synchronous HTTP calls (product, inventory, cart). If inventory is down, order returns 503.
- Check inventory health and logs: `docker compose logs inventory`
- Retry order after inventory recovers. Reserved stock is **not** leaked — order rolls back reservation on failure (see `services/order/app/main.py` reserve loop).

### Symptom: Cart empty after restart / `redis down`

- Cart uses Redis (in-memory). `redis down` → gateway health degraded.
- Check: `docker compose logs redis`, `docker exec ecom-redis redis-cli ping` → `PONG`
- If volume corrupted: `docker compose down -v` then `make up` (wipes DBs but cart is ephemeral by design — TTL 7d).

### Symptom: RabbitMQ 15672 not reachable

- Notification service is HTTP-first; RabbitMQ is optional (async future). Health still `healthy` if RabbitMQ down? No — `depends_on: condition: service_healthy` enforces RabbitMQ up. If you see `notification degraded`, check RabbitMQ: `docker compose logs rabbitmq`.

### Symptom: `Connection refused` on 5432

- Single Postgres holds 9 DBs. `init-db.sql` runs only on first volume creation.
- If you changed POSTGRES_PASSWORD and reused volume, auth fails. Fix: `docker compose down -v` (destroys data) then `make up`.
- Check DBs exist:
  ```bash
  docker exec ecom-postgres psql -U ecom -c "\l" | grep -E "identity|product|inventory|order|payment|shipping|notification|review"
  docker exec ecom-postgres psql -U ecom -d product_db -c "\dt"
  ```

### K8s-specific (Phase 4-5)

- **NetworkPolicy default-deny:** gateway needs egress to services; services need egress to postgres/redis/rabbitmq. If ArgoCD sync shows `NetworkPolicy` but curl fails inside pod, check:
  ```bash
  kubectl -n ecom get networkpolicy
  kubectl -n ecom exec deploy/gateway -- curl -v http://product:8002/health
  kubectl -n ecom exec deploy/product -- curl -v http://postgres:5432 # should fail — postgres port is 5432 not HTTP, but TCP should be open via policy
  ```
  Our Helm chart allows `gateway → *` and `* → postgres/redis/rabbitmq`.

- **Readiness vs Liveness:** liveness `curl /health` kills pod; readiness gates traffic. If product is still seeding (5s), gateway 502 is normal for 30s.

---

## 3. ARM64 / Graviton / Multi-arch

### Symptom: `exec format error` on AWS Graviton (m7g/m6g) or Apple Silicon when pulling `ecom-identity:latest`

**Cause:** image built on x86 (`linux/amd64`) without ARM variant. Graviton requires `linux/arm64`.

**Fix (Jenkins Phase 2):**

1. Builder:
   ```bash
   docker buildx create --name graviton --use
   docker buildx inspect --bootstrap
   ```

2. Build & push multi-arch (Jenkins does for all 10):
   ```bash
   docker buildx build --platform linux/amd64,linux/arm64 \
     -f services/identity/Dockerfile \
     -t yourdockerhub/ecom-identity:latest \
     --push services/identity
   # verify manifest
   docker buildx imagetools inspect yourdockerhub/ecom-identity:latest | grep -E "linux/amd64|linux/arm64"
   ```

3. Local dev (single arch, fast):
   ```bash
   docker compose build          # uses native arch only
   # or force:
   docker build --platform linux/arm64 -f services/identity/Dockerfile services/identity
   ```

### Symptom: `gcc failed` or `psycopg2` compile error on ARM

- Our Dockerfiles use `python:3.12-slim` + `psycopg2-binary` (precompiled wheels for both amd64 & arm64) + `gcc`/`libpq-dev` only to be safe. `psycopg2-binary` avoids compile. If you switched to `psycopg2` (source), it fails on Alpine but not on `slim`.
- Keep `psycopg2-binary` in `requirements.txt`. Do NOT use Alpine python base (musl needs build).

### Symptom: Slow t3 vs m7g cold start

- Graviton has faster boot; but if you see Jenkins `buildx` emulation slow (qemu), it's because you're building arm64 on x86 via qemu. Jenkins should use Graviton runners natively (or AWS CodeBuild ARM). Phase 3 Terraform provisions `m7g.medium` Jenkins agents for native arm64 builds.

### Symptom: Image pull `no matching manifest`

- Check tag: `docker pull --platform linux/arm64 yourdockerhub/ecom-product:latest`
- DockerHub manifest list must exist. Jenkins `buildx --push` creates it; plain `docker build` does not.

---

## 4. Common Local Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `port is already allocated 8080` | host collision | `lsof -i :8080` or change `docker-compose.yaml` ports `8081:8080` |
| `pg_isready` unhealthy | init-db.sql syntax error | `docker compose logs postgres` → check SQL; our script uses `\gexec` which requires PG 15 (we use 15-alpine ✓) |
| `redis-cli ping` fails | AOF file corrupt | `docker compose down -v` or `redis-cli --raw FLUSHALL` |
| `401 Could not validate credentials` | JWT secret mismatch | Identity & gateway must share `SECRET_KEY` (we default same value); check `.env` |
| `400 SKU already exists` | re-seed collides | Product SKU unique; delete: `curl -X DELETE http://localhost:8080/api/products/1` or `docker compose down -v` |
| `order 503 Product service unavailable` | product still starting | Wait `sleep 10` and retry; gateway retries 3× but order does 1× — make product `healthy` first |
| `payment always failed` | amount 666/999.99 are forced fails for testing | Use normal amount e.g. `24.50` |
| Logs flood `DB init failed` | postgres not ready yet | Normal for first 10s — services retry 5× with 3s sleep |

---

## 5. Clean Reset

```bash
# Nuclear (destroys DBs, carts):
docker compose down -v --remove-orphans
docker system prune -f
make up

# Soft (keeps volumes):
docker compose restart product inventory
docker compose logs -f --tail=50
```

---

## 6. Getting Help

- File issue with `docker compose ps`, `curl http://localhost:8080/health | jq`, `docker compose logs <failing-svc> --tail=100`
- Include manifest: `docker buildx imagetools inspect <image>`

Keep this file updated as you move to Kind (local K8s) and EKS Graviton — the DNS & ARM sections apply 1:1.

