from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal

class OrderItem(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)
    price: Optional[Decimal]=None

class CreateOrderRequest(BaseModel):
    user_id: str
    items: List[OrderItem]
    shipping_address: Optional[Dict[str,Any]]=None
    currency: str="USD"

class OrderResponse(BaseModel):
    id:int
    user_id:str
    status:str
    total_amount:Decimal
    currency:str
    items:List[Dict]
    shipping_address:Optional[Dict]
    payment_id:Optional[str]
    created_at:Optional[datetime]
    updated_at:Optional[datetime]
    class Config:
        from_attributes=True

class UpdateStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(pending|confirmed|cancelled|shipped|delivered|processing)$")
