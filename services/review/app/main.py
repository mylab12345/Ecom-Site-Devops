import logging, time, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import httpx

from .database import get_db, engine, Base
from .models import Review
from .schemas import ReviewCreate, ReviewUpdate, ReviewResponse
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

app=FastAPI(title="Review Service", version="1.0.0", lifespan=lifespan)
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

@app.post("/reviews", response_model=ReviewResponse, status_code=201)
async def create_review(payload: ReviewCreate, request: Request, db:Session=Depends(get_db)):
    # verify product exists
    try:
        async with httpx.AsyncClient(timeout=3.0, headers={"X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4()))}) as client:
            resp=await client.get(f"{settings.product_service_url}/products/{payload.product_id}")
            if resp.status_code==404:
                raise HTTPException(404,"Product not found")
            elif resp.status_code!=200:
                logger.warning(f"product verification failed {resp.status_code}")
    except HTTPException: raise
    except Exception as e:
        logger.warning(f"product check skipped {e}")

    existing=db.query(Review).filter(Review.product_id==payload.product_id, Review.user_id==payload.user_id).first()
    if existing:
        raise HTTPException(400,"User already reviewed this product. Use PUT to update.")

    review=Review(**payload.model_dump())
    db.add(review)
    try:
        db.commit(); db.refresh(review)
    except IntegrityError:
        db.rollback(); raise HTTPException(400,"Duplicate review")
    logger.info(f"Review created product={payload.product_id} user={payload.user_id} rating={payload.rating}")
    return review

@app.get("/reviews/{review_id}", response_model=ReviewResponse)
def get_review(review_id:int, db:Session=Depends(get_db)):
    r=db.query(Review).filter(Review.id==review_id).first()
    if not r: raise HTTPException(404,"Review not found")
    return r

@app.get("/reviews/product/{product_id}", response_model=list[ReviewResponse])
def list_by_product(product_id:int, skip:int=0, limit:int=50, min_rating:int=None, db:Session=Depends(get_db)):
    q=db.query(Review).filter(Review.product_id==product_id)
    if min_rating: q=q.filter(Review.rating>=min_rating)
    return q.order_by(Review.created_at.desc()).offset(skip).limit(limit).all()

@app.get("/reviews/user/{user_id}", response_model=list[ReviewResponse])
def list_by_user(user_id:str, db:Session=Depends(get_db)):
    return db.query(Review).filter(Review.user_id==user_id).order_by(Review.created_at.desc()).all()

@app.get("/reviews/product/{product_id}/stats")
def product_stats(product_id:int, db:Session=Depends(get_db)):
    rows=db.query(Review.rating, func.count(Review.id)).filter(Review.product_id==product_id).group_by(Review.rating).all()
    total=db.query(func.count(Review.id)).filter(Review.product_id==product_id).scalar()
    avg=db.query(func.avg(Review.rating)).filter(Review.product_id==product_id).scalar()
    distribution={str(r[0]): r[1] for r in rows}
    # fill missing
    for i in range(1,6):
        distribution.setdefault(str(i),0)
    return {
        "product_id":product_id,
        "total_reviews": total or 0,
        "average_rating": round(float(avg),2) if avg else 0,
        "distribution": distribution,
        "star_percent": {k: round(v/(total or 1)*100,1) for k,v in distribution.items()}
    }

@app.put("/reviews/{review_id}", response_model=ReviewResponse)
def update_review(review_id:int, payload: ReviewUpdate, db:Session=Depends(get_db)):
    r=db.query(Review).filter(Review.id==review_id).first()
    if not r: raise HTTPException(404,"Review not found")
    for k,v in payload.model_dump(exclude_unset=True).items():
        setattr(r,k,v)
    db.commit(); db.refresh(r)
    logger.info(f"Review {review_id} updated")
    return r

@app.delete("/reviews/{review_id}")
def delete_review(review_id:int, db:Session=Depends(get_db)):
    r=db.query(Review).filter(Review.id==review_id).first()
    if not r: raise HTTPException(404,"Review not found")
    db.delete(r); db.commit()
    return {"detail":"Review deleted"}

@app.get("/reviews")
def list_all(skip:int=0, limit:int=50, db:Session=Depends(get_db)):
    return db.query(Review).order_by(Review.created_at.desc()).offset(skip).limit(limit).all()

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8009)
