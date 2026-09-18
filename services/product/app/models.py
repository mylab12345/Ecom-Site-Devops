from sqlalchemy import Column, Integer, String, Text, Numeric, DateTime, Boolean, Index
from sqlalchemy.sql import func
from .database import Base

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), index=True, nullable=False)
    brand = Column(String(100), nullable=True)
    price = Column(Numeric(10,2), nullable=False)
    currency = Column(String(3), default="USD")
    image_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index('idx_product_category_price', 'category', 'price'),
        Index('idx_product_name_trgm', 'name'),
    )
