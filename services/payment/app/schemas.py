from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from decimal import Decimal

class PaymentCreate(BaseModel):
    order_id: int
    user_id: str
    amount: Decimal = Field(..., ge=0)
    currency: str="USD"
    method: str= Field(default="card", pattern="^(card|paypal|cod|upi)$")
    idempotency_key: Optional[str]=None

class PaymentProcess(BaseModel):
    force_status: Optional[str]=Field(None, pattern="^(success|failed)$")

class PaymentResponse(BaseModel):
    id:int
    order_id:int
    user_id:str
    amount:Decimal
    currency:str
    method:str
    status:str
    transaction_id:Optional[str]
    idempotency_key:Optional[str]
    provider_response:Optional[Dict[str,Any]]
    created_at:Optional[datetime]
    updated_at:Optional[datetime]
    class Config:
        from_attributes=True
