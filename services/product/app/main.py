import logging, time, uuid
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .database import get_db, engine, Base
from .models import Product
from .schemas import ProductCreate, ProductUpdate, ProductResponse
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(settings.service_name)

REQUEST_COUNT = Counter("http_requests_total", "Total requests", ["method","endpoint","status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["endpoint"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    for _ in range(5):
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("DB ready")
            break
        except Exception as e:
            logger.warning(f"DB init failed {e}")
            time.sleep(3)
    # Seed demo data if empty
    from sqlalchemy.orm import Session as Sess
    db = Sess(bind=engine)
    try:
        if db.query(Product).count() == 0:
            samples = [
                Product(sku="SKU-001", name="Wireless Headphones Pro", description="Active noise cancellation, 40h battery", category="Electronics", brand="SoundMax", price=199.99, image_url="https://via.placeholder.com/300"),
                Product(sku="SKU-002", name="Organic Cotton T-Shirt", description="100% organic cotton, unisex", category="Apparel", brand="EcoWear", price=29.99),
                Product(sku="SKU-003", name="Stainless Steel Water Bottle", description="750ml insulated", category="Home", brand="HydroFlask", price=24.50),
                Product(sku="SKU-004", name="Gaming Laptop X15", description="RTX 4070, 32GB RAM, 1TB SSD", category="Electronics", brand="GameRig", price=1499.00),
                Product(sku="SKU-005", name="Yoga Mat Premium", description="Non-slip 6mm thick", category="Sports", brand="ZenFit", price=49.99),
                Product(sku="SKU-006", name="Smart Watch Series 6", description="Heart rate, GPS, 7-day battery", category="Electronics", brand="TimeTech", price=249.00),
                Product(sku="SKU-007", name="Running Shoes Air", description="Breathable mesh, carbon plate", category="Sports", brand="RunFast", price=129.95),
                Product(sku="SKU-008", name="Coffee Maker Deluxe", description="Programmable 12-cup", category="Home", brand="BrewMaster", price=89.99),
            ]
            db.add_all(samples)
            db.commit()
            logger.info("Seeded 8 products")
    except Exception as e:
        logger.warning(f"Seeding failed {e}")
    finally:
        db.close()
    yield

app = FastAPI(title="Product Catalog Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.time()
    resp = await call_next(request)
    latency = time.time()-start
    REQUEST_COUNT.labels(method=request.method, endpoint=request.url.path, status=resp.status_code).inc()
    REQUEST_LATENCY.labels(endpoint=request.url.path).observe(latency)
    resp.headers["X-Request-ID"]=req_id
    resp.headers["X-Service"]=settings.service_name
    return resp

@app.get("/health")
def health(): return {"status":"healthy","service":settings.service_name}

@app.get("/metrics")
def metrics(): return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/products", response_model=ProductResponse, status_code=201)
def create_product(payload: ProductCreate, db: Session=Depends(get_db)):
    if db.query(Product).filter(Product.sku==payload.sku).first():
        raise HTTPException(400,"SKU already exists")
    prod = Product(**payload.model_dump())
    db.add(prod); db.commit(); db.refresh(prod)
    logger.info(f"Created product {prod.sku} id={prod.id}")
    return prod

@app.get("/products", response_model=List[ProductResponse])
def list_products(
    q: Optional[str]=Query(None, description="search term"),
    category: Optional[str]=None,
    brand: Optional[str]=None,
    min_price: Optional[float]=None,
    max_price: Optional[float]=None,
    skip: int=0,
    limit: int=20,
    sort_by: str=Query("id", pattern="^(id|price|name|created_at)$"),
    order: str=Query("asc", pattern="^(asc|desc)$"),
    db: Session=Depends(get_db)
):
    query = db.query(Product).filter(Product.is_active==True)
    if q:
        query = query.filter(or_(Product.name.ilike(f"%{q}%"), Product.description.ilike(f"%{q}%"), Product.sku.ilike(f"%{q}%")))
    if category: query = query.filter(Product.category==category)
    if brand: query = query.filter(Product.brand==brand)
    if min_price is not None: query = query.filter(Product.price>=min_price)
    if max_price is not None: query = query.filter(Product.price<=max_price)
    col = getattr(Product, sort_by)
    query = query.order_by(col.desc() if order=="desc" else col.asc())
    return query.offset(skip).limit(limit).all()

@app.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session=Depends(get_db)):
    prod = db.query(Product).filter(Product.id==product_id).first()
    if not prod: raise HTTPException(404,"Product not found")
    return prod

@app.put("/products/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, payload: ProductUpdate, db: Session=Depends(get_db)):
    prod = db.query(Product).filter(Product.id==product_id).first()
    if not prod: raise HTTPException(404,"Product not found")
    for k,v in payload.model_dump(exclude_unset=True).items():
        setattr(prod,k,v)
    db.commit(); db.refresh(prod)
    return prod

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session=Depends(get_db)):
    prod = db.query(Product).filter(Product.id==product_id).first()
    if not prod: raise HTTPException(404,"Product not found")
    # soft delete
    prod.is_active=False
    db.commit()
    return {"detail":"Product deactivated"}

@app.get("/categories/list")
def list_categories(db: Session=Depends(get_db)):
    cats = db.query(Product.category).distinct().all()
    return {"categories":[c[0] for c in cats]}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8002)
