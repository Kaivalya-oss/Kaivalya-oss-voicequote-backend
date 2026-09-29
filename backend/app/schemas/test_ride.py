from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.schemas.customer import CustomerResponse
from app.schemas.vehicle import VariantResponse
from app.schemas.auth import UserResponse


class TestRideBase(BaseModel):
    customer_id: int
    variant_id: int
    scheduled_at: datetime
    duration_minutes: int = Field(default=30, ge=15, le=120)
    driving_license_number: Optional[str] = Field(None, example="DL-1420110012345")
    notes: Optional[str] = None


class TestRideCreate(TestRideBase):
    pass


class TestRideUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, ge=15, le=120)
    status: Optional[str] = Field(None, pattern=r"^(REQUESTED|CONFIRMED|COMPLETED|CANCELLED|NO_SHOW)$")
    driving_license_number: Optional[str] = None
    feedback_rating: Optional[int] = Field(None, ge=1, le=5)
    notes: Optional[str] = None


class TestRideCompleteRequest(BaseModel):
    feedback_rating: int = Field(..., ge=1, le=5, description="Customer feedback rating (1 to 5 stars)")
    notes: Optional[str] = Field(None, description="Test ride feedback and observations")


class TestRideResponse(TestRideBase):
    id: int
    sales_executive_id: int
    status: str
    feedback_rating: Optional[int] = None
    customer: Optional[CustomerResponse] = None
    variant: Optional[VariantResponse] = None
    sales_executive: Optional[UserResponse] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
