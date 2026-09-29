from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.schemas.auth import UserResponse


class FollowupBase(BaseModel):
    lead_id: int
    scheduled_at: datetime
    type: str = Field(default="CALL", pattern=r"^(CALL|VISIT|WHATSAPP|EMAIL)$")
    notes: Optional[str] = None
    status: str = Field(default="SCHEDULED", pattern=r"^(SCHEDULED|COMPLETED|CANCELLED)$")


class FollowupCreate(BaseModel):
    lead_id: int
    scheduled_at: datetime
    type: str = Field(default="CALL", pattern=r"^(CALL|VISIT|WHATSAPP|EMAIL)$")
    notes: Optional[str] = None


class FollowupUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    type: Optional[str] = Field(None, pattern=r"^(CALL|VISIT|WHATSAPP|EMAIL)$")
    notes: Optional[str] = None
    status: Optional[str] = Field(None, pattern=r"^(SCHEDULED|COMPLETED|CANCELLED)$")


class FollowupCompleteRequest(BaseModel):
    notes: Optional[str] = Field(None, description="Outcome notes from the completed follow-up")


class FollowupResponse(FollowupBase):
    id: int
    assigned_user_id: int
    completed_at: Optional[datetime] = None
    assigned_user: Optional[UserResponse] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
