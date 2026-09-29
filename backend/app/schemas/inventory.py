from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.schemas.vehicle import VariantResponse


class InventoryUnitBase(BaseModel):
    variant_id: int
    vin_chassis_number: str = Field(..., min_length=5, max_length=50, example="ME4JF5077K8123456")
    engine_number: str = Field(..., min_length=5, max_length=50, example="JF50E9123456")
    color: str = Field(..., min_length=2, max_length=50, example="Pearl Nightstar Black")
    status: str = Field(default="AVAILABLE", example="AVAILABLE")
    # AVAILABLE, RESERVED, SOLD, SERVICE, IN_TRANSIT
    branch_name: str = Field(default="Main Showroom", example="Main Showroom")
    arrival_date: Optional[date] = None
    notes: Optional[str] = None


class InventoryUnitCreate(InventoryUnitBase):
    pass


class InventoryUnitUpdate(BaseModel):
    color: Optional[str] = None
    status: Optional[str] = None
    branch_name: Optional[str] = None
    arrival_date: Optional[date] = None
    notes: Optional[str] = None


class InventoryUnitResponse(InventoryUnitBase):
    id: int
    variant: Optional[VariantResponse] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class VariantStockSummary(BaseModel):
    variant_id: int
    variant_name: str
    total_units: int
    available_units: int
    reserved_units: int
    sold_units: int
    service_units: int
    in_transit_units: int


class StockActionRequest(BaseModel):
    notes: Optional[str] = Field(None, example="Reserved for quotation QT-20260930-1001")
