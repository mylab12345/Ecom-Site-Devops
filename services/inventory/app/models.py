from sqlalchemy import Column, Integer, String, DateTime, Index
from sqlalchemy.sql import func
from .database import Base

class Inventory(Base):
    __tablename__="inventory"
    id=Column(Integer, primary_key=True, index=True)
    product_id=Column(Integer, unique=True, index=True, nullable=False)
    sku=Column(String(50), nullable=True)
    quantity=Column(Integer, nullable=False, default=0)
    reserved=Column(Integer, nullable=False, default=0)  # reserved for pending orders
    warehouse=Column(String(100), default="WH-01")
    updated_at=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__=(Index('idx_inventory_product','product_id'),)

class StockMovement(Base):
    __tablename__="stock_movements"
    id=Column(Integer, primary_key=True, index=True)
    product_id=Column(Integer, index=True, nullable=False)
    delta=Column(Integer, nullable=False)
    reason=Column(String(255), nullable=True)
    created_at=Column(DateTime(timezone=True), server_default=func.now())
