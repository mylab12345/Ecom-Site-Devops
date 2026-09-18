import logging, time, uuid
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .config import settings
from jose import jwt, JWTError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger(settings.service_name)

REQUEST_COUNT=Counter("http_requests_total","Total",["method","endpoint","status"])
REQUEST_LATENCY=Histogram("http_request_duration_seconds","Latency",["endpoint"])
PROXY_ERRORS=Counter("gateway_proxy_errors_total","Proxy errors",["service"])

app=FastAPI(title="API Gateway", version="1.0.0", description="Central entry point for E-Commerce microservices")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Route mapping: prefix -> service key
ROUTES = {
    "/api/auth": "identity",
    "/api/users": "identity",
    "/auth": "identity",
    "/users": "identity",
    "/api/products": "product",
    "/products": "product",
    "/api/categories": "product",
    "/categories": "product",
    "/api/inventory": "inventory",
    "/inventory": "inventory",
    "/api/cart": "cart",
    "/cart": "cart",
    "/api/orders": "order",
    "/orders": "order",
    "/api/payments": "payment",
    "/payments": "payment",
    "/api/shipping": "shipping",
    "/shipments": "shipping",
    "/api/shipments": "shipping",
    "/api/notifications": "notification",
    "/notifications": "notification",
    "/api/reviews": "review",
    "/reviews": "review",
}

# Build longest prefix first
SORTED_ROUTES=sorted(ROUTES.items(), key=lambda x: len(x[0]), reverse=True)

def resolve_service(path: str):
    for prefix, svc in SORTED_ROUTES:
        if path.startswith(prefix):
            # strip /api prefix for downstream: keep as is but remove /api if backend expects without
            # Backends support both with and without /api via gateway stripping; we forward original path after stripping /api
            downstream_path=path
            if prefix.startswith("/api/"):
                # map /api/products -> /products
                downstream_path=path.replace("/api","",1)
                if not downstream_path.startswith("/"):
                    downstream_path="/"+downstream_path
            return svc, downstream_path
    return None, None

@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    # Inject correlation ID and timing for gateway itself
    request_id=request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id=request_id
    start=time.time()
    response=await call_next(request)
    latency=time.time()-start
    # Metrics for gateway-handled routes (health, metrics)
    REQUEST_COUNT.labels(method=request.method, endpoint=request.url.path, status=response.status_code).inc()
    REQUEST_LATENCY.labels(endpoint=request.url.path).observe(latency)
    response.headers["X-Request-ID"]=request_id
    response.headers["X-Gateway"]="api-gateway"
    return response

@app.get("/health")
async def health():
    # Aggregate health from all services
    results={}
    async with httpx.AsyncClient(timeout=2.0) as client:
        for name, base in settings.services.items():
            try:
                resp=await client.get(f"{base}/health")
                results[name]={"status":"up" if resp.status_code==200 else "down","code":resp.status_code,"body":resp.json() if resp.headers.get("content-type","").startswith("application/json") else resp.text[:200]}
            except Exception as e:
                results[name]={"status":"down","error":str(e)}
    overall="healthy" if all(v["status"]=="up" for v in results.values()) else "degraded"
    return {"status":overall,"gateway":"api-gateway","services":results}

@app.get("/metrics")
def metrics(): return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/")
def root():
    return {
        "service":"api-gateway",
        "version":"1.0.0",
        "docs":"/docs",
        "health":"/health",
        "routes":[{"prefix":k,"service":v, "target":settings.services[v]} for k,v in ROUTES.items()],
        "message":"E-Commerce Microservices Gateway - forward requests to /api/*"
    }

# Proxy all other paths
@app.api_route("/{path:path}", methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS","HEAD"])
async def proxy(path: str, request: Request):
    full_path="/" + path
    # exclude gateway's own endpoints
    if full_path in ["/health","/metrics","/","/docs","/openapi.json","/redoc"]:
        raise HTTPException(404,"Not found")

    query=request.url.query
    svc, downstream_path = resolve_service(full_path)
    if not svc:
        return JSONResponse(status_code=404, content={"detail":f"No route for {full_path}","hint":"Use /api/* prefixes","available":list(ROUTES.keys())})

    target_base=settings.services[svc]
    url=f"{target_base}{downstream_path}"
    if query: url+=f"?{query}"

    # Headers forwarding (preserve correlation)
    headers=dict(request.headers)
    headers.pop("host",None)
    headers.pop("content-length",None)
    headers["X-Request-ID"]=request.state.request_id
    headers["X-Forwarded-By"]="api-gateway"
    # Optional JWT pass-through validation (if enforce_auth enabled, verify token locally)
    if settings.enforce_auth and full_path.startswith("/api/") and full_path not in ["/api/auth/login","/api/auth/register","/api/products","/api/reviews"]:
        auth=headers.get("authorization","")
        if not auth.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"detail":"Missing bearer token"})
        token=auth.split(" ",1)[1]
        try:
            jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        except JWTError as e:
            return JSONResponse(status_code=401, content={"detail":f"Invalid token: {e}"})

    body=await request.body()

    # Retry logic for ARM64/graviton cold start resilience
    last_exc=None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp=await client.request(request.method, url, headers=headers, content=body)
                # Build response
                excluded_headers={"content-encoding","content-length","transfer-encoding","connection"}
                resp_headers={k:v for k,v in resp.headers.items() if k.lower() not in excluded_headers}
                resp_headers["X-Request-ID"]=request.state.request_id
                resp_headers["X-Proxied-Service"]=svc
                # Metrics
                REQUEST_COUNT.labels(method=request.method, endpoint=f"/proxy/{svc}", status=resp.status_code).inc()
                if resp.status_code>=500:
                    PROXY_ERRORS.labels(service=svc).inc()
                return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers, media_type=resp.headers.get("content-type"))
        except httpx.RequestError as e:
            last_exc=e
            logger.warning(f"Proxy attempt {attempt+1}/3 to {svc} {url} failed: {e}")
            time.sleep(0.5* (attempt+1))
            continue
    PROXY_ERRORS.labels(service=svc).inc()
    logger.error(f"Proxy failed to {svc} {url}: {last_exc}")
    return JSONResponse(status_code=502, content={"detail":f"Bad Gateway to {svc}","service":svc,"url":url,"error":str(last_exc),"hint":"Check Kubernetes DNS: svc.cluster.local and docker-compose service name"})

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8080)
