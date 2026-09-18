from sqlalchemy import Column, Integer, String, Numeric, DateTime, JSON, Index
from sqlalchemy.sql import func
from .database import Base

class Order(Base):
    __tablename__="orders"
    id=Column(Integer, primary_key=True, index=True)
    user_id=Column(String(100), index=True, nullable=False)  # string to support uuid or int
    status=Column(String(50), default="pending", index=True)  # pending, confirmed, cancelled, shipped, delivered
    total_amount=Column(Numeric(10,2), nullable=False, default=0)
    currency=Column(String(3), default="USD")
    items=Column(JSON, nullable=False)  # [{"product_id":1, "quantity":2, "price":9.99}]
    shipping_address=Column(JSON, nullable=True)
    payment_id=Column(String(100), nullable=True)
    created_at=Column(DateTime(timezone=True), server_default=func.now())
    updated_at=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__=(Index('idx_order_user_status','user_id','status'),)
