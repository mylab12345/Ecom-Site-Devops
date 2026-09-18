from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ReviewCreate(BaseModel):
    product_id: int
    user_id: str
    rating: int = Field(..., ge=1, le=5)
    title: Optional[str]=None
    comment: Optional[str]=None
    order_id: Optional[int]=None

class ReviewUpdate(BaseModel):
    rating: Optional[int]=Field(None, ge=1, le=5)
    title: Optional[str]=None
    comment: Optional[str]=None

class ReviewResponse(BaseModel):
    id:int
    product_id:int
    user_id:str
    order_id:Optional[int]
    rating:int
    title:Optional[str]
    comment:Optional[str]
    created_at:Optional[datetime]
    updated_at:Optional[datetime]
    class Config:
        from_attributes=True
