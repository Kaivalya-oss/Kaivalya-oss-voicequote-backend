from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator


def _assume_utc(v: Optional[datetime]) -> Optional[datetime]:
    """Treat timezone-less datetimes as UTC so comparisons never mix naive and aware values."""
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v

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

    _utc = field_validator("scheduled_at")(_assume_utc)


class FollowupUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    type: Optional[str] = Field(None, pattern=r"^(CALL|VISIT|WHATSAPP|EMAIL)$")
    notes: Optional[str] = None
    # Accepted only for backwards compatibility; changing status here is rejected (use /complete or /cancel).
    status: Optional[str] = Field(None, pattern=r"^(SCHEDULED|COMPLETED|CANCELLED)$")

    _utc = field_validator("scheduled_at")(_assume_utc)


class FollowupCancelRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Why the follow-up is no longer needed")


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
