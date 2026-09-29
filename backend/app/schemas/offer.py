from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class OfferBase(BaseModel):
    title: str = Field(..., min_length=2, max_length=150, example="Festive Season Discount")
    description: Optional[str] = Field(None, example="₹5,000 flat discount on Activa 125")
    discount_type: str = Field(..., pattern=r"^(PERCENTAGE|FIXED)$", example="FIXED")
    discount_value: Decimal = Field(..., ge=Decimal("0.01"), example=Decimal("5000.00"))
    valid_from: date = Field(..., example="2026-09-01")
    valid_until: date = Field(..., example="2026-10-31")
    applicable_brand_id: Optional[int] = None
    applicable_model_id: Optional[int] = None
    applicable_variant_id: Optional[int] = None
    min_order_value: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    active: bool = True

    @model_validator(mode="after")
    def validate_dates_and_discounts(self) -> "OfferBase":
        if self.valid_until < self.valid_from:
            raise ValueError("valid_until must be on or after valid_from")
        if self.discount_type == "PERCENTAGE" and self.discount_value > Decimal("100.00"):
            raise ValueError("Percentage discount cannot exceed 100%")
        return self


class OfferCreate(OfferBase):
    pass


class OfferUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=150)
    description: Optional[str] = None
    discount_type: Optional[str] = Field(None, pattern=r"^(PERCENTAGE|FIXED)$")
    discount_value: Optional[Decimal] = Field(None, ge=Decimal("0.01"))
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    applicable_brand_id: Optional[int] = None
    applicable_model_id: Optional[int] = None
    applicable_variant_id: Optional[int] = None
    min_order_value: Optional[Decimal] = Field(None, ge=Decimal("0"))
    active: Optional[bool] = None


class OfferResponse(OfferBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
