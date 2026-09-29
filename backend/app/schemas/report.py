from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel


class SalesReportItem(BaseModel):
    quotation_id: int
    quotation_number: str
    customer_name: str
    variant_name: str
    sales_executive_name: str
    final_price: Decimal
    status: str
    created_at: datetime


class LeadReportItem(BaseModel):
    lead_id: int
    customer_name: str
    phone: str
    status: str
    priority: str
    source: str
    assigned_salesperson_name: str
    interested_vehicle: Optional[str] = None
    created_at: datetime


class InventoryReportItem(BaseModel):
    unit_id: int
    vin_chassis_number: str
    engine_number: str
    variant_name: str
    color: str
    status: str
    branch_name: str
    on_road_price: Decimal
