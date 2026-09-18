import logging, time, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from .database import get_db, engine, Base
from .models import Notification
from .schemas import NotificationCreate, NotificationResponse
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger(settings.service_name)
REQUEST_COUNT=Counter("http_requests_total","Total",["method","endpoint","status"])
REQUEST_LATENCY=Histogram("http_request_duration_seconds","Latency",["endpoint"])
NOTIF_SENT=Counter("notifications_sent_total","Sent",["type","channel"])

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

app=FastAPI(title="Notification Service", version="1.0.0", lifespan=lifespan)
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

def simulate_send(n: Notification) -> str:
    # Simulate provider latency and logging
    if n.type=="email":
        logger.info(f"[MOCK EMAIL] to user {n.user_id}: {n.title} - {n.message[:80]}")
        return "email sent via SES mock"
    elif n.type=="sms":
        logger.info(f"[MOCK SMS] to user {n.user_id}: {n.message[:80]}")
        return "sms sent via SNS mock"
    else:
        logger.info(f"[MOCK PUSH] to user {n.user_id}: {n.title}")
        return "push sent via FCM mock"

@app.post("/notifications/send", response_model=NotificationResponse, status_code=201)
def send_notification(payload: NotificationCreate, db:Session=Depends(get_db)):
    notification=Notification(user_id=payload.user_id, type=payload.type, channel=payload.channel, title=payload.title, message=payload.message, is_sent=False)
    db.add(notification); db.flush()
    try:
        provider_resp=simulate_send(notification)
        notification.is_sent=True
        notification.provider_response=provider_resp
    except Exception as e:
        logger.error(f"send failed {e}")
        notification.provider_response=str(e)
    db.commit(); db.refresh(notification)
    NOTIF_SENT.labels(type=payload.type, channel=payload.channel).inc()
    logger.info(f"Notification {notification.id} user={payload.user_id} type={payload.type} sent={notification.is_sent}")
    return notification

@app.get("/notifications/{notification_id}", response_model=NotificationResponse)
def get_notification(notification_id:int, db:Session=Depends(get_db)):
    n=db.query(Notification).filter(Notification.id==notification_id).first()
    if not n: raise HTTPException(404,"Notification not found")
    return n

@app.get("/notifications/user/{user_id}", response_model=list[NotificationResponse])
def list_user_notifications(user_id:str, skip:int=0, limit:int=50, unread_only:bool=False, db:Session=Depends(get_db)):
    q=db.query(Notification).filter(Notification.user_id==user_id)
    if unread_only: q=q.filter(Notification.is_read==False)
    return q.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

@app.get("/notifications", response_model=list[NotificationResponse])
def list_all(skip:int=0, limit:int=50, db:Session=Depends(get_db)):
    return db.query(Notification).order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

@app.put("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_read(notification_id:int, db:Session=Depends(get_db)):
    n=db.query(Notification).filter(Notification.id==notification_id).first()
    if not n: raise HTTPException(404,"Notification not found")
    n.is_read=True
    db.commit(); db.refresh(n)
    return n

@app.post("/notifications/bulk")
def bulk_send(payloads: list[NotificationCreate], db:Session=Depends(get_db)):
    results=[]
    for p in payloads:
        n=Notification(user_id=p.user_id, type=p.type, channel=p.channel, title=p.title, message=p.message)
        n.provider_response=simulate_send(n)
        n.is_sent=True
        db.add(n); db.flush()
        results.append(n.id)
        NOTIF_SENT.labels(type=p.type, channel=p.channel).inc()
    db.commit()
    return {"sent":len(results),"ids":results}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8008)
