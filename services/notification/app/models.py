from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Index
from sqlalchemy.sql import func
from .database import Base

class Notification(Base):
    __tablename__="notifications"
    id=Column(Integer, primary_key=True, index=True)
    user_id=Column(String(100), index=True, nullable=False)
    type=Column(String(50), default="email") # email, sms, push
    channel=Column(String(50), default="email")
    title=Column(String(255), nullable=False)
    message=Column(Text, nullable=False)
    is_read=Column(Boolean, default=False)
    is_sent=Column(Boolean, default=False)
    provider_response=Column(Text, nullable=True)
    created_at=Column(DateTime(timezone=True), server_default=func.now())
    __table_args__=(Index('idx_notif_user_created','user_id','created_at'),)
