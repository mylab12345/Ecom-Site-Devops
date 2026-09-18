from sqlalchemy import Column, Integer, String, Numeric, DateTime, JSON, Index
from sqlalchemy.sql import func
from .database import Base

class Payment(Base):
    __tablename__="payments"
    id=Column(Integer, primary_key=True, index=True)
    order_id=Column(Integer, index=True, nullable=False)
    user_id=Column(String(100), index=True, nullable=False)
    amount=Column(Numeric(10,2), nullable=False)
    currency=Column(String(3), default="USD")
    method=Column(String(50), default="card") # card, paypal, cod
    status=Column(String(50), default="pending", index=True) # pending, success, failed, refunded
    transaction_id=Column(String(100), unique=True, index=True, nullable=True)
    idempotency_key=Column(String(100), unique=True, index=True, nullable=True)
    provider_response=Column(JSON, nullable=True)
    created_at=Column(DateTime(timezone=True), server_default=func.now())
    updated_at=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__=(Index('idx_payment_order_status','order_id','status'),)
