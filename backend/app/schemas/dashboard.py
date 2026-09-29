from decimal import Decimal
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class DashboardSummary(BaseModel):
    total_customers: int
    total_leads: int
    quotations_generated: int
    quotations_accepted: int
    active_bookings: int
    completed_purchases: int
    total_revenue: Decimal
    pending_followups: int
    available_inventory: int
    reserved_inventory: int
    total_test_rides: int
    lead_conversion_rate_percentage: Decimal


class SalesDashboardMetrics(BaseModel):
    total_quotations: int
    accepted_quotations: int
    rejected_quotations: int
    conversion_rate_percentage: Decimal
    sales_by_variant: List[Dict[str, int]]
    sales_by_executive: List[Dict[str, int]]


class LeadsDashboardMetrics(BaseModel):
    total_leads: int
    leads_by_status: Dict[str, int]
    leads_by_source: Dict[str, int]
    leads_by_priority: Dict[str, int]


class InventoryDashboardMetrics(BaseModel):
    total_units: int
    available_units: int
    reserved_units: int
    sold_units: int
    service_units: int
    in_transit_units: int
    stock_by_brand: List[Dict[str, int]]


class RevenueDashboardMetrics(BaseModel):
    total_revenue: Decimal
    average_quotation_value: Decimal
    quotation_value_pipeline: Decimal
    revenue_by_variant: List[Dict[str, Decimal]]
