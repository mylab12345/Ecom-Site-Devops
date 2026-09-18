import logging, time, uuid, json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import httpx

from .database import get_db, engine, Base
from .models import Order
from .schemas import CreateOrderRequest, OrderResponse, UpdateStatusRequest
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger(settings.service_name)
REQUEST_COUNT=Counter("http_requests_total","Total",["method","endpoint","status"])
REQUEST_LATENCY=Histogram("http_request_duration_seconds","Latency",["endpoint"])
ORDER_CREATED=Counter("orders_created_total","Orders created")

@asynccontextmanager
async def lifespan(app: FastAPI):
    for _ in range(5):
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("DB ready")
            break
        except Exception as e:
            logger.warning(e); time.sleep(3)
    yield

app=FastAPI(title="Order Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

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
def health(): return {"status":"healthy","service":settings.service_name}
@app.get("/metrics")
def metrics(): return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

async def validate_and_enrich_items(items, request_id: str):
    enriched=[]
    total=0
    async with httpx.AsyncClient(timeout=5.0, headers={"X-Request-ID": request_id}) as client:
        for it in items:
            # product lookup
            try:
                resp=await client.get(f"{settings.product_service_url}/products/{it.product_id}")
                if resp.status_code!=200:
                    raise HTTPException(404, f"Product {it.product_id} not found")
                prod=resp.json()
                price = float(it.price) if it.price is not None else float(prod["price"])
            except HTTPException: raise
            except Exception as e:
                logger.warning(f"product service error {e}")
                price=float(it.price or 0)
                if price==0:
                    raise HTTPException(503, f"Product service unavailable for {it.product_id}")

            # inventory check/reserve
            try:
                # check availability before reserve
                inv_resp=await client.get(f"{settings.inventory_service_url}/inventory/{it.product_id}")
                if inv_resp.status_code==200:
                    inv=inv_resp.json()
                    available=inv.get("available",0)
                    if available < it.quantity:
                        raise HTTPException(409, f"Insufficient stock for product {it.product_id}: available {available}")
            except HTTPException: raise
            except Exception as e:
                logger.warning(f"inventory check failed {e}")

            enriched.append({"product_id": it.product_id, "quantity": it.quantity, "price": price, "name": prod.get("name") if 'prod' in locals() else None})
            total+= price * it.quantity
    return enriched, round(total,2)

@app.post("/orders", response_model=OrderResponse, status_code=201)
async def create_order(payload: CreateOrderRequest, request: Request, db:Session=Depends(get_db)):
    request_id=request.headers.get("X-Request-ID", str(uuid.uuid4()))
    if not payload.items: raise HTTPException(400,"Order must have at least one item")

    enriched, total = await validate_and_enrich_items(payload.items, request_id)

    # Reserve inventory for all items
    async with httpx.AsyncClient(timeout=5.0, headers={"X-Request-ID": request_id}) as client:
        reserved=[]
        try:
            for it in enriched:
                resp=await client.post(f"{settings.inventory_service_url}/inventory/reserve", json={"product_id":it["product_id"],"quantity":it["quantity"],"order_id":"temp"})
                if resp.status_code!=200:
                    raise HTTPException(409, f"Failed to reserve product {it['product_id']}: {resp.text}")
                reserved.append(it)
        except HTTPException as e:
            # rollback reserved
            for r in reserved:
                try: await client.post(f"{settings.inventory_service_url}/inventory/release", json={"product_id":r["product_id"],"quantity":r["quantity"]})
                except: pass
            raise e

    order=Order(user_id=payload.user_id, status="pending", total_amount=total, currency=payload.currency, items=enriched, shipping_address=payload.shipping_address)
    db.add(order); db.commit(); db.refresh(order)
    logger.info(f"Order created id={order.id} user={order.user_id} total={total}")

    # Update reserved with real order_id (best effort)
    async with httpx.AsyncClient(timeout=5.0) as client:
        pass  # already reserved with temp, could re-link

    # Fire-and-forget: notify shipping & notification via background?
    # For Phase 1 we do synchronous commit attempt after payment simulation
    ORDER_CREATED.inc()

    # Optionally clear cart
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.delete(f"{settings.cart_service_url}/cart/{payload.user_id}")
    except Exception as e:
        logger.debug(f"cart clear failed {e}")

    return order

@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id:int, db:Session=Depends(get_db)):
    order=db.query(Order).filter(Order.id==order_id).first()
    if not order: raise HTTPException(404,"Order not found")
    return order

@app.get("/orders/user/{user_id}", response_model=list[OrderResponse])
def list_user_orders(user_id:str, skip:int=0, limit:int=50, db:Session=Depends(get_db)):
    return db.query(Order).filter(Order.user_id==user_id).order_by(Order.created_at.desc()).offset(skip).limit(limit).all()

@app.get("/orders", response_model=list[OrderResponse])
def list_orders(skip:int=0, limit:int=50, status: str=None, db:Session=Depends(get_db)):
    q=db.query(Order)
    if status: q=q.filter(Order.status==status)
    return q.order_by(Order.created_at.desc()).offset(skip).limit(limit).all()

@app.put("/orders/{order_id}/status", response_model=OrderResponse)
async def update_status(order_id:int, payload: UpdateStatusRequest, request: Request, db:Session=Depends(get_db)):
    order=db.query(Order).filter(Order.id==order_id).first()
    if not order: raise HTTPException(404,"Order not found")
    prev=order.status
    order.status=payload.status
    db.commit(); db.refresh(order)
    logger.info(f"Order {order_id} status {prev} -> {payload.status}")

    request_id=request.headers.get("X-Request-ID", str(uuid.uuid4()))
    # side effects
    if payload.status=="confirmed":
        # commit inventory
        async with httpx.AsyncClient(timeout=5.0, headers={"X-Request-ID": request_id}) as client:
            for it in order.items:
                try:
                    await client.post(f"{settings.inventory_service_url}/inventory/commit", json={"product_id":it["product_id"],"quantity":it["quantity"],"order_id":str(order.id)})
                except Exception as e: logger.warning(f"inventory commit failed {e}")
        # create shipment
        try:
            async with httpx.AsyncClient(timeout=5.0, headers={"X-Request-ID": request_id}) as client:
                await client.post(f"{settings.shipping_service_url}/shipments", json={"order_id":order.id, "user_id":order.user_id, "address":order.shipping_address or {}, "items":order.items})
        except Exception as e: logger.warning(f"shipping create failed {e}")
    elif payload.status=="cancelled":
        # release inventory if still pending
        if prev=="pending":
            async with httpx.AsyncClient(timeout=5.0, headers={"X-Request-ID": request_id}) as client:
                for it in order.items:
                    try: await client.post(f"{settings.inventory_service_url}/inventory/release", json={"product_id":it["product_id"],"quantity":it["quantity"]})
                    except: pass
    # notification
    try:
        async with httpx.AsyncClient(timeout=3.0, headers={"X-Request-ID": request_id}) as client:
            await client.post(f"{settings.notification_service_url}/notifications/send", json={"user_id":order.user_id,"type":"email","channel":"email","title":f"Order {order.id} {payload.status}","message":f"Your order #{order.id} is now {payload.status}. Total ${order.total_amount}"})
    except Exception as e: logger.debug(f"notification failed {e}")

    return order

@app.delete("/orders/{order_id}")
def cancel_order(order_id:int, db:Session=Depends(get_db)):
    order=db.query(Order).filter(Order.id==order_id).first()
    if not order: raise HTTPException(404,"Order not found")
    if order.status in ["shipped","delivered"]: raise HTTPException(400,"Cannot cancel shipped/delivered order")
    order.status="cancelled"
    db.commit(); db.refresh(order)
    return order

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8005)
