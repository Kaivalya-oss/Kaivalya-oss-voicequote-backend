from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, computed_field, field_validator

from app.schemas.customer import CustomerResponse
from app.schemas.vehicle import VariantResponse
from app.schemas.offer import OfferResponse
from app.schemas.auth import UserResponse


PaymentMode = Literal["CASH", "FINANCE"]

# Older comparison snapshots still contain these keys; they are never returned.
LEGACY_FINANCE_KEYS = {"down_payment", "loan_amount", "interest_rate", "loan_tenure", "emi"}


class QuotationItemCreate(BaseModel):
    accessory_id: Optional[int] = None
    item_name: str = Field(..., min_length=2, max_length=150, example="All-Weather Floor Mat")

    @field_validator("item_name", mode="before")
    @classmethod
    def _strip_name(cls, v):
        return v.strip() if isinstance(v, str) else v

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
    details_json: Optional[Dict[str, Any]] = None

    @field_validator("details_json")
    @classmethod
    def _drop_legacy_finance(cls, v):
        return {k: val for k, val in v.items() if k not in LEGACY_FINANCE_KEYS} if v else v

    class Config:
        from_attributes = True


class QuotationCreate(BaseModel):
    customer_id: int
    variant_id: int
    accessory_ids: Optional[List[int]] = Field(default_factory=list, description="IDs of standard accessories from catalog")
    custom_items: Optional[List[QuotationItemCreate]] = Field(default_factory=list, description="Optional custom accessory items")
    offer_id: Optional[int] = Field(None, description="Optional applicable promotional offer ID")
    payment_mode: PaymentMode = Field(default="CASH", description="CASH or FINANCE; shared by both options of a comparison")
    notes: Optional[str] = None
    # For multiple vehicle comparison within one quotation:
    compare_variant_id: Optional[int] = Field(None, description="Optional second vehicle variant ID for side-by-side comparison")
    compare_offer_id: Optional[int] = Field(None, description="Optional applicable promotional offer ID for the second vehicle")

    @field_validator("accessory_ids")
    @classmethod
    def _unique_accessories(cls, v):
        if v and len(v) != len(set(v)):
            raise ValueError("accessory_ids contains duplicates; each accessory can be added once")
        return v


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
    # Option A only when is_comparison is true; use the VEHICLE_OPTION items for prices.
    final_price: Decimal
    payment_mode: PaymentMode

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

    @computed_field
    @property
    def is_comparison(self) -> bool:
        return sum(1 for it in self.items if it.item_type == "VEHICLE_OPTION") >= 2

    class Config:
        from_attributes = True
