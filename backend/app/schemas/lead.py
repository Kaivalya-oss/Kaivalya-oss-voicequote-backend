from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from app.schemas.customer import CustomerResponse
from app.schemas.auth import UserResponse
from app.schemas.vehicle import VariantResponse


class LeadStatusHistoryResponse(BaseModel):
    id: int
    from_status: str
    to_status: str
    changed_by_user_id: Optional[int] = None
    reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LeadBase(BaseModel):
    customer_id: int
    assigned_salesperson_id: Optional[int] = None
    source: str = Field(default="WALK_IN", pattern=r"^(WALK_IN|ONLINE|REFERRAL|PHONE_INQUIRY|CAMPAIGN)$")
    status: str = Field(default="NEW", pattern=r"^(NEW|CONTACTED|INTERESTED|TEST_RIDE|NEGOTIATION|BOOKED|PURCHASED|LOST)$")
    priority: str = Field(default="MEDIUM", pattern=r"^(LOW|MEDIUM|HIGH|URGENT)$")
    interested_variant_id: Optional[int] = None
    expected_purchase_date: Optional[date] = None
    next_followup_date: Optional[datetime] = None
    notes: Optional[str] = None


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    assigned_salesperson_id: Optional[int] = None
    source: Optional[str] = Field(None, pattern=r"^(WALK_IN|ONLINE|REFERRAL|PHONE_INQUIRY|CAMPAIGN)$")
    priority: Optional[str] = Field(None, pattern=r"^(LOW|MEDIUM|HIGH|URGENT)$")
    interested_variant_id: Optional[int] = None
    expected_purchase_date: Optional[date] = None
    next_followup_date: Optional[datetime] = None
    notes: Optional[str] = None


class LeadStatusTransitionRequest(BaseModel):
    to_status: str = Field(..., pattern=r"^(NEW|CONTACTED|INTERESTED|TEST_RIDE|NEGOTIATION|BOOKED|PURCHASED|LOST)$")
    reason: Optional[str] = Field(None, description="Reason for status change or loss explanation")


class LeadResponse(LeadBase):
    id: int
    customer: Optional[CustomerResponse] = None
    assigned_salesperson: Optional[UserResponse] = None
    interested_variant: Optional[VariantResponse] = None
    status_history: List[LeadStatusHistoryResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
