from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class NotificationCreate(BaseModel):
    user_id: str
    type: str = Field(default="email", pattern="^(email|sms|push)$")
    channel: str = Field(default="email")
    title: str
    message: str

class NotificationResponse(BaseModel):
    id:int
    user_id:str
    type:str
    channel:str
    title:str
    message:str
    is_read:bool
    is_sent:bool
    provider_response:Optional[str]
    created_at:Optional[datetime]
    class Config:
        from_attributes=True
