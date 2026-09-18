from sqlalchemy import Column, Integer, String, Text, DateTime, Index, UniqueConstraint
from sqlalchemy.sql import func
from .database import Base

class Review(Base):
    __tablename__="reviews"
    id=Column(Integer, primary_key=True, index=True)
    product_id=Column(Integer, index=True, nullable=False)
    user_id=Column(String(100), index=True, nullable=False)
    order_id=Column(Integer, nullable=True)
    rating=Column(Integer, nullable=False) # 1-5
    title=Column(String(255), nullable=True)
    comment=Column(Text, nullable=True)
    created_at=Column(DateTime(timezone=True), server_default=func.now())
    updated_at=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__=(
        Index('idx_review_product_rating','product_id','rating'),
        UniqueConstraint('product_id','user_id', name='uq_product_user'),
    )
