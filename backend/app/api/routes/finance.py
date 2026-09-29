from fastapi import APIRouter
from app.schemas.common import SuccessResponse
from app.schemas.finance import EMICalculateRequest, EMICalculateResponse
from app.services.emi_service import EMIService

router = APIRouter(prefix="/finance", tags=["Finance & EMI"])


@router.post(
    "/calculate-emi",
    response_model=SuccessResponse[EMICalculateResponse],
    summary="Calculate loan EMI with exact banking Decimal formulas",
)
async def calculate_emi(req: EMICalculateRequest):
    result = EMIService.calculate(
        principal=req.principal,
        annual_interest_rate=req.annual_interest_rate,
        tenure_months=req.tenure_months,
    )
    return SuccessResponse(data=result)
