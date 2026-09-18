import logging
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from .database import get_db, engine, Base
from .models import User
from .schemas import UserCreate, UserResponse, Token
from .auth import verify_password, get_password_hash, create_access_token, get_current_active_user, get_current_user
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(settings.service_name)

# Prometheus metrics
REQUEST_COUNT = Counter("http_requests_total", "Total requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["endpoint"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    for _ in range(5):
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("Database tables created/verified")
            break
        except Exception as e:
            logger.warning(f"DB init failed, retrying: {e}")
            time.sleep(3)
    yield

app = FastAPI(title="Identity Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_request_id_and_metrics(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.time()
    response = await call_next(request)
    latency = time.time() - start
    REQUEST_COUNT.labels(method=request.method, endpoint=request.url.path, status=response.status_code).inc()
    REQUEST_LATENCY.labels(endpoint=request.url.path).observe(latency)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Service"] = settings.service_name
    return response

@app.get("/health")
def health():
    return {"status": "healthy", "service": settings.service_name, "version": "1.0.0"}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter((User.email == user.email) | (User.username == user.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email or username already registered")
    hashed = get_password_hash(user.password)
    db_user = User(email=user.email, username=user.username, hashed_password=hashed, full_name=user.full_name)
    db.add(db_user)
    try:
        db.commit()
        db.refresh(db_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="User already exists")
    logger.info(f"User registered: {db_user.username} id={db_user.id}")
    return db_user

@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    token = create_access_token(data={"sub": user.username, "user_id": user.id, "is_admin": user.is_admin})
    logger.info(f"User login: {user.username}")
    return {"access_token": token, "token_type": "bearer", "user": user}

@app.post("/auth/verify")
def verify_token(current_user: User = Depends(get_current_user)):
    return {"valid": True, "user": UserResponse.model_validate(current_user).model_dump()}

@app.get("/users/me", response_model=UserResponse)
def read_me(current_user: User = Depends(get_current_active_user)):
    return current_user

@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@app.get("/users", response_model=list[UserResponse])
def list_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(User).offset(skip).limit(limit).all()

@app.put("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: int, payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.id != user_id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if "full_name" in payload:
        user.full_name = payload["full_name"]
    if "is_active" in payload and current_user.is_admin:
        user.is_active = payload["is_active"]
    db.commit()
    db.refresh(user)
    return user

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
