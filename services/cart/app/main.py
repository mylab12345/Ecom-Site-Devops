import logging, time, uuid, json
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import redis
import httpx
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger(settings.service_name)

REQUEST_COUNT=Counter("http_requests_total","Total",["method","endpoint","status"])
REQUEST_LATENCY=Histogram("http_request_duration_seconds","Latency",["endpoint"])

app=FastAPI(title="Cart Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Redis client with retry
r=None
for _ in range(10):
    try:
        r=redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=5)
        r.ping()
        logger.info("Connected to Redis")
        break
    except Exception as e:
        logger.warning(f"Redis connect failed: {e}")
        time.sleep(2)
if r is None:
    r=redis.from_url(settings.redis_url, decode_responses=True)

@app.middleware("http")
async def mw(request: Request, call_next):
    rid=request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start=time.time()
    resp=await call_next(request)
    REQUEST_COUNT.labels(method=request.method, endpoint=request.url.path, status=resp.status_code).inc()
    REQUEST_LATENCY.labels(endpoint=request.url.path).observe(time.time()-start)
    resp.headers["X-Request-ID"]=rid
    resp.headers["X-Service"]=settings.service_name
    return resp

@app.get("/health")
def health():
    try: r.ping(); redis_status="up"
    except: redis_status="down"
    return {"status":"healthy" if redis_status=="up" else "degraded","service":settings.service_name,"redis":redis_status}

@app.get("/metrics")
def metrics(): return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

class AddItemRequest(BaseModel):
    product_id: int
    quantity: int = 1
    price: Optional[float]=None  # optional override, else fetch from product service

class CartItem(BaseModel):
    product_id:int
    quantity:int
    price:Optional[float]=None
    name:Optional[str]=None
    added_at:Optional[str]=None

def cart_key(user_id: str) -> str: return f"cart:{user_id}"
def cart_ttl(): return 60*60*24*7  # 7 days

async def enrich_items(items: List[Dict]) -> List[Dict]:
    # Try to enrich with product service
    enriched=[]
    async with httpx.AsyncClient(timeout=3.0) as client:
        for it in items:
            try:
                resp=await client.get(f"{settings.product_service_url}/products/{it['product_id']}")
                if resp.status_code==200:
                    prod=resp.json()
                    it["name"]=prod.get("name")
                    it["price"]=it.get("price") or float(prod.get("price",0))
                    it["image_url"]=prod.get("image_url")
            except Exception as e:
                logger.debug(f"enrich failed {e}")
            enriched.append(it)
    return enriched

@app.post("/cart/{user_id}/items")
async def add_item(user_id: str, payload: AddItemRequest):
    if payload.quantity <=0: raise HTTPException(400,"Quantity must be >0")
    key=cart_key(user_id)
    raw=r.get(key)
    cart=json.loads(raw) if raw else {"user_id":user_id, "items":[]}
    # if price not provided, fetch
    price=payload.price
    if price is None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp=await client.get(f"{settings.product_service_url}/products/{payload.product_id}")
                if resp.status_code==200:
                    price=float(resp.json()["price"])
        except Exception as e:
            logger.warning(f"product fetch failed {e}")
    # update or add
    found=False
    for it in cart["items"]:
        if it["product_id"]==payload.product_id:
            it["quantity"]+=payload.quantity
            if price is not None: it["price"]=price
            it["added_at"]=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            found=True; break
    if not found:
        cart["items"].append({"product_id":payload.product_id,"quantity":payload.quantity,"price":price,"added_at":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    r.setex(key, cart_ttl(), json.dumps(cart))
    logger.info(f"Cart {user_id} add product {payload.product_id} qty {payload.quantity}")
    enriched=await enrich_items(cart["items"])
    cart["items"]=enriched
    return cart

@app.get("/cart/{user_id}")
async def get_cart(user_id: str):
    key=cart_key(user_id)
    raw=r.get(key)
    if not raw: return {"user_id":user_id,"items":[],"total":0,"count":0}
    cart=json.loads(raw)
    enriched=await enrich_items(cart["items"])
    cart["items"]=enriched
    # compute totals
    total=sum((it.get("price") or 0)*it["quantity"] for it in cart["items"])
    count=sum(it["quantity"] for it in cart["items"])
    cart["total"]=round(total,2)
    cart["count"]=count
    return cart

@app.delete("/cart/{user_id}/items/{product_id}")
def remove_item(user_id: str, product_id:int):
    key=cart_key(user_id)
    raw=r.get(key)
    if not raw: raise HTTPException(404,"Cart not found")
    cart=json.loads(raw)
    orig=len(cart["items"])
    cart["items"]=[it for it in cart["items"] if it["product_id"]!=product_id]
    if len(cart["items"])==orig: raise HTTPException(404,"Item not in cart")
    r.setex(key, cart_ttl(), json.dumps(cart))
    logger.info(f"Cart {user_id} removed {product_id}")
    return cart

@app.put("/cart/{user_id}/items/{product_id}")
def update_item(user_id: str, product_id:int, quantity:int):
    if quantity <=0: raise HTTPException(400,"Quantity must be >0")
    key=cart_key(user_id)
    raw=r.get(key)
    if not raw: raise HTTPException(404,"Cart not found")
    cart=json.loads(raw)
    for it in cart["items"]:
        if it["product_id"]==product_id:
            it["quantity"]=quantity
            r.setex(key, cart_ttl(), json.dumps(cart))
            return cart
    raise HTTPException(404,"Item not in cart")

@app.delete("/cart/{user_id}")
def clear_cart(user_id: str):
    key=cart_key(user_id)
    r.delete(key)
    return {"detail":"Cart cleared","user_id":user_id}

@app.get("/cart/{user_id}/count")
def cart_count(user_id: str):
    raw=r.get(cart_key(user_id))
    if not raw: return {"count":0}
    cart=json.loads(raw)
    return {"count": sum(it["quantity"] for it in cart["items"])}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8004)
