import logging, time, uuid, random
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import httpx

from .database import get_db, engine, Base
from .models import Shipment
from .schemas import ShipmentCreate, ShipmentUpdate, ShipmentResponse
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger(settings.service_name)
REQUEST_COUNT=Counter("http_requests_total","Total",["method","endpoint","status"])
REQUEST_LATENCY=Histogram("http_request_duration_seconds","Latency",["endpoint"])

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

app=FastAPI(title="Shipping Service", version="1.0.0", lifespan=lifespan)
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

def gen_tracking(): return f"TRK{uuid.uuid4().hex[:12].upper()}"
def next_estimate(): return datetime.now(timezone.utc) + timedelta(days=random.randint(2,7))

@app.post("/shipments", response_model=ShipmentResponse, status_code=201)
async def create_shipment(payload: ShipmentCreate, request: Request, db:Session=Depends(get_db)):
    if db.query(Shipment).filter(Shipment.order_id==payload.order_id).first():
        raise HTTPException(400,"Shipment already exists for this order")
    tracking=gen_tracking()
    events=[{"status":"pending","timestamp":datetime.now(timezone.utc).isoformat(),"location":"Warehouse","message":"Shipment created"}]
    shipment=Shipment(order_id=payload.order_id, user_id=payload.user_id, tracking_number=tracking, carrier=payload.carrier, status="pending", address=payload.address, items=payload.items, estimated_delivery=next_estimate(), events=events)
    db.add(shipment); db.commit(); db.refresh(shipment)
    logger.info(f"Shipment created order={payload.order_id} tracking={tracking}")
    # notify
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{settings.notification_service_url}/notifications/send", json={"user_id":payload.user_id,"type":"email","channel":"email","title":f"Shipment created #{tracking}","message":f"Your order #{payload.order_id} is being prepared. Tracking {tracking}"}, headers={"X-Request-ID":request.headers.get("X-Request-ID", str(uuid.uuid4()))})
    except Exception as e: logger.debug(f"notify failed {e}")
    return shipment

@app.get("/shipments/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id:int, db:Session=Depends(get_db)):
    s=db.query(Shipment).filter(Shipment.id==shipment_id).first()
    if not s: raise HTTPException(404,"Shipment not found")
    return s

@app.get("/shipments/order/{order_id}", response_model=ShipmentResponse)
def get_by_order(order_id:int, db:Session=Depends(get_db)):
    s=db.query(Shipment).filter(Shipment.order_id==order_id).first()
    if not s: raise HTTPException(404,"Shipment not found for order")
    return s

@app.get("/shipments/track/{tracking_number}", response_model=ShipmentResponse)
def track(tracking_number:str, db:Session=Depends(get_db)):
    s=db.query(Shipment).filter(Shipment.tracking_number==tracking_number).first()
    if not s: raise HTTPException(404,"Tracking not found")
    return s

@app.get("/shipments", response_model=list[ShipmentResponse])
def list_shipments(skip:int=0, limit:int=50, status:str=None, db:Session=Depends(get_db)):
    q=db.query(Shipment)
    if status: q=q.filter(Shipment.status==status)
    return q.order_by(Shipment.created_at.desc()).offset(skip).limit(limit).all()

@app.put("/shipments/{shipment_id}/status", response_model=ShipmentResponse)
async def update_status(shipment_id:int, payload: ShipmentUpdate, request: Request, db:Session=Depends(get_db)):
    s=db.query(Shipment).filter(Shipment.id==shipment_id).first()
    if not s: raise HTTPException(404,"Shipment not found")
    valid_transitions={
        "pending":["shipped","cancelled"],
        "shipped":["out_for_delivery","delivered","returned"],
        "out_for_delivery":["delivered","returned"],
        "delivered":[],
        "returned":[],
        "cancelled":[]
    }
    if payload.status not in valid_transitions.get(s.status, []):
        # allow any for demo but log warning
        logger.warning(f"Unusual transition {s.status}->{payload.status}")

    s.status=payload.status
    evt={"status":payload.status,"timestamp":datetime.now(timezone.utc).isoformat(),"location":payload.location or "Hub","message":f"Status updated to {payload.status}"}
    events=list(s.events or [])
    events.append(evt)
    s.events=events
    db.commit(); db.refresh(s)
    logger.info(f"Shipment {shipment_id} status -> {payload.status}")

    # propagate to order service and notify
    try:
        order_status="shipped" if payload.status=="shipped" else "delivered" if payload.status=="delivered" else None
        if order_status:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.put(f"{settings.order_service_url}/orders/{s.order_id}/status", json={"status":order_status}, headers={"X-Request-ID":request.headers.get("X-Request-ID", str(uuid.uuid4()))})
    except Exception as e: logger.debug(f"order sync failed {e}")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{settings.notification_service_url}/notifications/send", json={"user_id":s.user_id,"type":"email","channel":"email","title":f"Shipment {s.tracking_number} {payload.status}","message":f"Your shipment is {payload.status} at {evt['location']}"})
    except: pass
    return s

@app.get("/shipments/{shipment_id}/events")
def get_events(shipment_id:int, db:Session=Depends(get_db)):
    s=db.query(Shipment).filter(Shipment.id==shipment_id).first()
    if not s: raise HTTPException(404,"Shipment not found")
    return {"tracking_number":s.tracking_number,"events":s.events}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8007)
