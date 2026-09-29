from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field


class VehicleRecommendationRequest(BaseModel):
    budget: Optional[Decimal] = Field(None, ge=Decimal("0"), example=Decimal("120000.00"), description="Max or approximate budget")
    preferred_brand: Optional[str] = Field(None, example="Honda", description="Preferred vehicle brand")
    usage: str = Field(default="COMMUTE", example="COMMUTE", description="Primary usage: COMMUTE, TOURING, COLLEGE, DELIVERY, FAMILY")
    preferred_fuel_type: Optional[str] = Field(None, example="PETROL", description="PETROL, ELECTRIC, HYBRID")
    expected_emi: Optional[Decimal] = Field(None, ge=Decimal("0"), example=Decimal("3000.00"), description="Target monthly EMI")
    preferences: Optional[str] = Field(None, example="High mileage, lightweight, disc brakes, digital console")


class RecommendedVehicle(BaseModel):
    variant_id: int
    variant_name: str
    brand_name: str
    engine_cc: Optional[int] = None
    fuel_type: str
    on_road_price: Decimal
    estimated_emi: Decimal
    fit_score: int = Field(..., ge=1, le=100)
    match_reason: str


class VehicleRecommendationResponse(BaseModel):
    recommendations: List[RecommendedVehicle]
    ai_analysis: str
    provider: str


class LeadInsightsResponse(BaseModel):
    lead_id: int
    customer_name: str
    current_status: str
    conversion_probability: str  # HIGH, MEDIUM, LOW
    interest_summary: str
    recommended_followup_timing: str
    suggested_next_action: str
    relevant_vehicle_suggestions: List[str]
    provider: str
