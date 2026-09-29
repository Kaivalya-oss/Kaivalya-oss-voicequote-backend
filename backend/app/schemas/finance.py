from decimal import Decimal
from pydantic import BaseModel, Field


class EMICalculateRequest(BaseModel):
    principal: Decimal = Field(..., gt=Decimal("0"), example=Decimal("100000.00"), description="Loan amount (Principal > 0)")
    annual_interest_rate: Decimal = Field(..., ge=Decimal("0"), example=Decimal("9.50"), description="Annual interest rate percentage (>= 0)")
    tenure_months: int = Field(..., gt=0, le=120, example=36, description="Tenure in months (1 - 120)")


class EMICalculateResponse(BaseModel):
    monthly_emi: Decimal = Field(..., example=Decimal("3203.32"))
    total_interest: Decimal = Field(..., example=Decimal("15319.52"))
    total_payable: Decimal = Field(..., example=Decimal("115319.52"))
    principal: Decimal = Field(..., example=Decimal("100000.00"))
    tenure_months: int = Field(..., example=36)
    annual_interest_rate: Decimal = Field(..., example=Decimal("9.50"))
