from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BrandBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, example="Honda")
    country: Optional[str] = Field(None, example="Japan")
    logo_url: Optional[str] = Field(None, example="https://example.com/honda.png")
    is_active: bool = True


class BrandCreate(BrandBase):
    pass


class BrandResponse(BrandBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ModelBase(BaseModel):
    brand_id: int
    name: str = Field(..., min_length=2, max_length=100, example="Activa")
    vehicle_type: str = Field(default="SCOOTER", example="SCOOTER")  # SCOOTER, MOTORCYCLE, EV
    description: Optional[str] = None
    is_active: bool = True


class ModelCreate(ModelBase):
    pass


class ModelResponse(ModelBase):
    id: int
    brand: Optional[BrandResponse] = None
    created_at: datetime

    class Config:
        from_attributes = True


class VariantBase(BaseModel):
    model_id: int
    name: str = Field(..., min_length=2, max_length=100, example="Activa 125 DLX")
    code: str = Field(..., min_length=2, max_length=50, example="ACTIVA-125-DLX")
    engine_cc: Optional[int] = Field(None, ge=50, le=2500, example=125)
    fuel_type: str = Field(default="PETROL", example="PETROL")  # PETROL, ELECTRIC, HYBRID
    transmission: str = Field(default="AUTOMATIC", example="AUTOMATIC")  # MANUAL, AUTOMATIC, CVT
    ex_showroom_price: Decimal = Field(..., ge=Decimal("0.01"), example=Decimal("89000.00"))
    insurance_price: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), example=Decimal("6500.00"))
    rto_price: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), example=Decimal("9200.00"))
    accessories_price: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), example=Decimal("2500.00"))
    color_options: Optional[str] = Field(None, example="Pearl Nightstar Black, Rebel Red Metallic")
    status: str = Field(default="ACTIVE", example="ACTIVE")  # ACTIVE, DISCONTINUED
    specs_json: Optional[Dict[str, Any]] = None


class VariantCreate(VariantBase):
    pass


class VariantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    engine_cc: Optional[int] = Field(None, ge=50, le=2500)
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    ex_showroom_price: Optional[Decimal] = Field(None, ge=Decimal("0.01"))
    insurance_price: Optional[Decimal] = Field(None, ge=Decimal("0"))
    rto_price: Optional[Decimal] = Field(None, ge=Decimal("0"))
    accessories_price: Optional[Decimal] = Field(None, ge=Decimal("0"))
    color_options: Optional[str] = None
    status: Optional[str] = None
    specs_json: Optional[Dict[str, Any]] = None


class VariantResponse(VariantBase):
    id: int
    on_road_price: Decimal
    model: Optional[ModelResponse] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AccessoryBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=150, example="Helmet Lock")
    part_number: Optional[str] = Field(None, example="ACC-HL-01")
    price: Decimal = Field(..., ge=Decimal("0.01"), example=Decimal("650.00"))
    description: Optional[str] = None
    is_active: bool = True


class AccessoryCreate(AccessoryBase):
    pass


class AccessoryResponse(AccessoryBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
