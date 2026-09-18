from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class ShipmentCreate(BaseModel):
    order_id: int
    user_id: str
    address: Optional[Dict[str,Any]]=None
    items: Optional[List[Dict]]=None
    carrier: str="FedEx"

class ShipmentUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|shipped|out_for_delivery|delivered|returned|cancelled)$")
    location: Optional[str]=None

class ShipmentResponse(BaseModel):
    id:int
    order_id:int
    user_id:str
    tracking_number:str
    carrier:str
    status:str
    address:Optional[Dict]
    items:Optional[List[Dict]]
    estimated_delivery:Optional[datetime]
    events:Optional[List[Dict]]
    created_at:Optional[datetime]
    updated_at:Optional[datetime]
    class Config:
        from_attributes=True
