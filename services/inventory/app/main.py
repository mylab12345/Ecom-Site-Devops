import logging, time, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .database import get_db, engine, Base
from .models import Inventory, StockMovement
from .schemas import AdjustRequest, ReserveRequest, ReleaseRequest
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
    # seed
    from sqlalchemy.orm import Session as S
    db=S(bind=engine)
    try:
        if db.query(Inventory).count()==0:
            for pid in range(1,9):
                db.add(Inventory(product_id=pid, sku=f"SKU-00{pid}", quantity=100+pid*10, reserved=0, warehouse="WH-01"))
            db.commit()
            logger.info("Seeded inventory")
    except Exception as e: logger.warning(f"seed fail {e}")
    finally: db.close()
    yield

app=FastAPI(title="Inventory Service", version="1.0.0", lifespan=lifespan)
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

def _to_response(inv: Inventory):
    return {
        "id":inv.id,
        "product_id":inv.product_id,
        "sku":inv.sku,
        "quantity":inv.quantity,
        "reserved":inv.reserved,
        "available": inv.quantity - inv.reserved,
        "warehouse":inv.warehouse,
        "updated_at":inv.updated_at
    }

@app.get("/inventory/{product_id}")
def get_inventory(product_id:int, db:Session=Depends(get_db)):
    inv=db.query(Inventory).filter(Inventory.product_id==product_id).first()
    if not inv: raise HTTPException(404,"Inventory not found")
    return _to_response(inv)

@app.get("/inventory")
def list_inventory(skip:int=0, limit:int=100, db:Session=Depends(get_db)):
    invs=db.query(Inventory).offset(skip).limit(limit).all()
    return [_to_response(i) for i in invs]

@app.post("/inventory/{product_id}/adjust")
def adjust_stock(product_id:int, payload:AdjustRequest, db:Session=Depends(get_db)):
    inv=db.query(Inventory).filter(Inventory.product_id==product_id).first()
    if not inv:
        inv=Inventory(product_id=product_id, sku=payload.sku or f"SKU-{product_id}", quantity=0)
        db.add(inv); db.flush()
    new_qty=inv.quantity + payload.delta
    if new_qty < inv.reserved:
        raise HTTPException(400, f"Cannot reduce below reserved ({inv.reserved})")
    if new_qty <0:
        raise HTTPException(400,"Quantity cannot be negative")
    inv.quantity=new_qty
    if payload.sku: inv.sku=payload.sku
    db.add(StockMovement(product_id=product_id, delta=payload.delta, reason=payload.reason))
    db.commit(); db.refresh(inv)
    logger.info(f"Adjust product {product_id} delta {payload.delta} new {inv.quantity}")
    return _to_response(inv)

@app.post("/inventory/reserve")
def reserve_stock(payload:ReserveRequest, db:Session=Depends(get_db)):
    inv=db.query(Inventory).filter(Inventory.product_id==payload.product_id).first()
    if not inv: raise HTTPException(404,"Inventory not found")
    available=inv.quantity - inv.reserved
    if available < payload.quantity:
        raise HTTPException(409, f"Insufficient stock: available {available}, requested {payload.quantity}")
    inv.reserved += payload.quantity
    db.add(StockMovement(product_id=payload.product_id, delta=-payload.quantity, reason=f"reserve order {payload.order_id}"))
    db.commit(); db.refresh(inv)
    logger.info(f"Reserved {payload.quantity} for product {payload.product_id} order {payload.order_id}")
    return _to_response(inv)

@app.post("/inventory/release")
def release_stock(payload:ReleaseRequest, db:Session=Depends(get_db)):
    inv=db.query(Inventory).filter(Inventory.product_id==payload.product_id).first()
    if not inv: raise HTTPException(404,"Inventory not found")
    if inv.reserved < payload.quantity:
        raise HTTPException(400,"Release exceeds reserved")
    inv.reserved -= payload.quantity
    db.add(StockMovement(product_id=payload.product_id, delta=payload.quantity, reason="release"))
    db.commit(); db.refresh(inv)
    return _to_response(inv)

@app.post("/inventory/commit")
def commit_stock(payload:ReserveRequest, db:Session=Depends(get_db)):
    """Commit reserved stock: deduct from quantity and reserved (after order confirmed)"""
    inv=db.query(Inventory).filter(Inventory.product_id==payload.product_id).first()
    if not inv: raise HTTPException(404,"Inventory not found")
    if inv.reserved < payload.quantity: raise HTTPException(400,"Commit exceeds reserved")
    if inv.quantity < payload.quantity: raise HTTPException(400,"Insufficient quantity")
    inv.reserved -= payload.quantity
    inv.quantity -= payload.quantity
    db.add(StockMovement(product_id=payload.product_id, delta=-payload.quantity, reason=f"commit order {payload.order_id}"))
    db.commit(); db.refresh(inv)
    logger.info(f"Committed {payload.quantity} for product {payload.product_id}")
    return _to_response(inv)

@app.get("/inventory/{product_id}/movements")
def movements(product_id:int, limit:int=20, db:Session=Depends(get_db)):
    moves=db.query(StockMovement).filter(StockMovement.product_id==product_id).order_by(StockMovement.created_at.desc()).limit(limit).all()
    return [{"id":m.id,"product_id":m.product_id,"delta":m.delta,"reason":m.reason,"created_at":m.created_at} for m in moves]

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8003)
