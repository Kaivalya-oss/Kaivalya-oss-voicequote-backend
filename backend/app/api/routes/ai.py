from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.ai import (
    LeadInsightsResponse,
    VehicleRecommendationRequest,
    VehicleRecommendationResponse,
)
from app.schemas.common import SuccessResponse
from app.services.ai_service import AIService

router = APIRouter(prefix="/ai", tags=["AI Features"])


@router.post(
    "/recommend-vehicle",
    response_model=SuccessResponse[VehicleRecommendationResponse],
    summary="AI-powered vehicle match recommendations",
)
async def recommend_vehicle(
    req: VehicleRecommendationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AIService(db)
    result = await service.recommend_vehicles(req)
    return SuccessResponse(data=result)


@router.post(
    "/lead-insights/{lead_id}",
    response_model=SuccessResponse[LeadInsightsResponse],
    summary="AI-powered lead conversion insights and recommended next action",
)
async def get_lead_insights(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AIService(db)
    result = await service.get_lead_insights(lead_id)
    return SuccessResponse(data=result)
