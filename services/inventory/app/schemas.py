from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class InventoryResponse(BaseModel):
    id:int
    product_id:int
    sku:Optional[str]
    quantity:int
    reserved:int
    available:int
    warehouse:str
    updated_at:Optional[datetime]
    class Config:
        from_attributes=True

class AdjustRequest(BaseModel):
    delta:int
    reason:Optional[str]=None
    sku:Optional[str]=None

class ReserveRequest(BaseModel):
    product_id:int
    quantity:int
    order_id:Optional[str]=None

class ReleaseRequest(BaseModel):
    product_id:int
    quantity:int
