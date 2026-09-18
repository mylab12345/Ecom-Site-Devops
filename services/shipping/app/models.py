from sqlalchemy import Column, Integer, String, DateTime, JSON, Index
from sqlalchemy.sql import func
from .database import Base

class Shipment(Base):
    __tablename__="shipments"
    id=Column(Integer, primary_key=True, index=True)
    order_id=Column(Integer, index=True, nullable=False, unique=True)
    user_id=Column(String(100), index=True, nullable=False)
    tracking_number=Column(String(100), unique=True, index=True, nullable=False)
    carrier=Column(String(50), default="FedEx")
    status=Column(String(50), default="pending") # pending, shipped, out_for_delivery, delivered, returned
    address=Column(JSON, nullable=True)
    items=Column(JSON, nullable=True)
    estimated_delivery=Column(DateTime(timezone=True), nullable=True)
    events=Column(JSON, nullable=True, default=list) # [{"status":..., "timestamp":..., "location":...}]
    created_at=Column(DateTime(timezone=True), server_default=func.now())
    updated_at=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__=(Index('idx_shipment_order_tracking','order_id','tracking_number'),)
