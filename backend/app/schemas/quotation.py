from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field

from app.schemas.customer import CustomerResponse
from app.schemas.vehicle import VariantResponse
from app.schemas.offer import OfferResponse
from app.schemas.auth import UserResponse


class QuotationItemCreate(BaseModel):
    accessory_id: Optional[int] = None
    item_name: str = Field(..., min_length=2, max_length=150, example="All-Weather Floor Mat")
    item_type: str = Field(default="ACCESSORY", example="ACCESSORY")
    unit_price: Decimal = Field(..., ge=Decimal("0.00"), example=Decimal("850.00"))
    quantity: int = Field(default=1, ge=1, example=1)


class QuotationItemResponse(BaseModel):
    id: int
    item_name: str
    item_type: str
    unit_price: Decimal
    quantity: int
    total_price: Decimal

    class Config:
        from_attributes = True


class QuotationCreate(BaseModel):
    customer_id: int
    variant_id: int
    accessory_ids: Optional[List[int]] = Field(default_factory=list, description="IDs of standard accessories from catalog")
    custom_items: Optional[List[QuotationItemCreate]] = Field(default_factory=list, description="Optional custom accessory items")
    offer_id: Optional[int] = Field(None, description="Optional applicable promotional offer ID")
    down_payment: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), example=Decimal("25000.00"))
    loan_tenure: int = Field(default=36, ge=0, le=120, example=36, description="Tenure in months (0 for full cash payment)")
    interest_rate: Decimal = Field(default=Decimal("9.50"), ge=Decimal("0"), example=Decimal("9.50"))
    notes: Optional[str] = None


class QuotationStatusUpdate(BaseModel):
    status: str = Field(..., pattern=r"^(DRAFT|GENERATED|SENT|ACCEPTED|REJECTED|EXPIRED|CANCELLED)$")
    reason: Optional[str] = None


class QuotationResponse(BaseModel):
    id: int
    quotation_number: str
    customer_id: int
    sales_executive_id: int
    variant_id: int
    offer_id: Optional[int] = None

    # Pricing breakdown
    base_price: Decimal
    insurance: Decimal
    rto: Decimal
    accessories: Decimal
    discount: Decimal
    final_price: Decimal

    # Finance breakdown
    down_payment: Decimal
    loan_amount: Decimal
    interest_rate: Decimal
    loan_tenure: int
    emi: Decimal

    status: str
    pdf_url: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    customer: Optional[CustomerResponse] = None
    sales_executive: Optional[UserResponse] = None
    variant: Optional[VariantResponse] = None
    offer: Optional[OfferResponse] = None
    items: List[QuotationItemResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True
