import logging, time, uuid, random
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import httpx

from .database import get_db, engine, Base
from .models import Payment
from .schemas import PaymentCreate, PaymentResponse, PaymentProcess
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger(settings.service_name)
REQUEST_COUNT=Counter("http_requests_total","Total",["method","endpoint","status"])
REQUEST_LATENCY=Histogram("http_request_duration_seconds","Latency",["endpoint"])
PAYMENT_SUCCESS=Counter("payments_success_total","Success payments")
PAYMENT_FAILED=Counter("payments_failed_total","Failed payments")

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

app=FastAPI(title="Payment Service", version="1.0.0", lifespan=lifespan)
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

def _simulate_provider(amount: float, method: str) -> tuple[str, dict]:
    # Simulate 95% success unless amount > 2000 or method cod with large amount
    # Deterministic failure for amount == 666 or 999.99 for testing
    if amount in [666, 999.99]:
        return "failed", {"provider":"mock","reason":"Simulated decline","code":"DECLINED"}
    if method=="cod" and amount>500:
        return "failed", {"provider":"mock","reason":"COD limit exceeded"}
    # 5% random failure
    if random.random() < 0.05:
        return "failed", {"provider":"mock","reason":"Network timeout at acquirer"}
    return "success", {"provider":"mock","auth_code":str(uuid.uuid4())[:8].upper(), "network":"visa"}

@app.post("/payments", response_model=PaymentResponse, status_code=201)
async def create_payment(payload: PaymentCreate, request: Request, db:Session=Depends(get_db), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    key = payload.idempotency_key or idempotency_key or str(uuid.uuid4())
    # idempotency check
    existing=db.query(Payment).filter(Payment.idempotency_key==key).first()
    if existing:
        logger.info(f"Idempotent hit key={key} payment={existing.id}")
        return existing

    # optionally verify order exists
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp=await client.get(f"{settings.order_service_url}/orders/{payload.order_id}", headers={"X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4()))})
            if resp.status_code==200:
                order=resp.json()
                if float(order["total_amount"]) != float(payload.amount):
                    logger.warning(f"Amount mismatch order {order['total_amount']} vs payload {payload.amount}")
            elif resp.status_code==404:
                logger.warning(f"Order {payload.order_id} not found but allowing payment for demo")
    except Exception as e:
        logger.debug(f"order verification skipped {e}")

    txn_id=f"txn_{uuid.uuid4().hex[:16]}"
    payment=Payment(order_id=payload.order_id, user_id=payload.user_id, amount=payload.amount, currency=payload.currency, method=payload.method, status="pending", transaction_id=txn_id, idempotency_key=key, provider_response={"initiated":True})
    db.add(payment); db.commit(); db.refresh(payment)
    logger.info(f"Payment created id={payment.id} txn={txn_id} order={payload.order_id}")

    # Auto-process if not cod? For demo we keep pending until explicit process, but also provide auto-process option via sync call
    return payment

@app.post("/payments/{payment_id}/process", response_model=PaymentResponse)
async def process_payment(payment_id:int, payload: PaymentProcess = PaymentProcess(), request: Request = None, db:Session=Depends(get_db)):
    payment=db.query(Payment).filter(Payment.id==payment_id).first()
    if not payment: raise HTTPException(404,"Payment not found")
    if payment.status in ["success","refunded"]: return payment
    # simulate processing delay
    time.sleep(0.5)
    if payload.force_status:
        new_status=payload.force_status
        provider={"forced":True, "status":new_status}
    else:
        new_status, provider = _simulate_provider(float(payment.amount), payment.method)
    payment.status=new_status
    payment.provider_response=provider
    db.commit(); db.refresh(payment)
    if new_status=="success":
        PAYMENT_SUCCESS.inc()
        # notify order service to confirm order
        try:
            async with httpx.AsyncClient(timeout=5.0, headers={"X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4())) if request else {}}) as client:
                await client.put(f"{settings.order_service_url}/orders/{payment.order_id}/status", json={"status":"confirmed"})
                await client.post(f"{settings.notification_service_url}/notifications/send", json={"user_id":payment.user_id,"type":"email","channel":"email","title":f"Payment success order #{payment.order_id}","message":f"Payment {payment.transaction_id} succeeded for ${payment.amount}"})
        except Exception as e: logger.warning(f"post-payment hook failed {e}")
    else:
        PAYMENT_FAILED.inc()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.put(f"{settings.order_service_url}/orders/{payment.order_id}/status", json={"status":"cancelled"})
        except: pass
    logger.info(f"Payment {payment_id} processed -> {new_status}")
    return payment

@app.get("/payments/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id:int, db:Session=Depends(get_db)):
    p=db.query(Payment).filter(Payment.id==payment_id).first()
    if not p: raise HTTPException(404,"Payment not found")
    return p

@app.get("/payments/order/{order_id}", response_model=list[PaymentResponse])
def list_by_order(order_id:int, db:Session=Depends(get_db)):
    return db.query(Payment).filter(Payment.order_id==order_id).all()

@app.get("/payments", response_model=list[PaymentResponse])
def list_payments(skip:int=0, limit:int=50, db:Session=Depends(get_db)):
    return db.query(Payment).order_by(Payment.created_at.desc()).offset(skip).limit(limit).all()

@app.post("/payments/webhook")
async def webhook(payload: dict, db:Session=Depends(get_db)):
    # Simulate provider webhook
    txn=payload.get("transaction_id")
    status=payload.get("status")
    if not txn or not status: raise HTTPException(400,"transaction_id and status required")
    p=db.query(Payment).filter(Payment.transaction_id==txn).first()
    if not p: raise HTTPException(404,"Payment not found for txn")
    p.status=status
    p.provider_response=payload
    db.commit()
    return {"detail":"webhook processed","payment_id":p.id}

@app.post("/payments/{payment_id}/refund", response_model=PaymentResponse)
def refund(payment_id:int, db:Session=Depends(get_db)):
    p=db.query(Payment).filter(Payment.id==payment_id).first()
    if not p: raise HTTPException(404,"Payment not found")
    if p.status!="success": raise HTTPException(400,"Only successful payments can be refunded")
    p.status="refunded"
    p.provider_response={"refunded":True, "refund_id":f"ref_{uuid.uuid4().hex[:8]}"}
    db.commit(); db.refresh(p)
    logger.info(f"Payment {payment_id} refunded")
    return p

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8006)
