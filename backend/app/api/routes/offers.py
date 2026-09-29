from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import MANAGEMENT_ROLES, ALL_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import MessageResponse, SuccessResponse
from app.schemas.offer import OfferCreate, OfferResponse, OfferUpdate
from app.services.offer_service import OfferService

router = APIRouter(prefix="/offers", tags=["Offers"])


@router.get(
    "",
    response_model=SuccessResponse[List[OfferResponse]],
    summary="List available promotional offers",
)
async def list_offers(
    active_only: bool = Query(True, description="Filter only active offers"),
    brand_id: Optional[int] = Query(None, description="Filter by brand"),
    model_id: Optional[int] = Query(None, description="Filter by model"),
    variant_id: Optional[int] = Query(None, description="Filter by variant"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = OfferService(db)
    offers = await service.list_offers(
        active_only=active_only,
        brand_id=brand_id,
        model_id=model_id,
        variant_id=variant_id,
    )
    return SuccessResponse(data=[OfferResponse.model_validate(o) for o in offers])


@router.get(
    "/{id}",
    response_model=SuccessResponse[OfferResponse],
    summary="Get offer details by ID",
)
async def get_offer(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = OfferService(db)
    offer = await service.get_offer(id)
    return SuccessResponse(data=OfferResponse.model_validate(offer))


@router.post(
    "",
    response_model=SuccessResponse[OfferResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new promotional offer (Admin/Owner)",
)
async def create_offer(
    req: OfferCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = OfferService(db)
    offer = await service.create_offer(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=OfferResponse.model_validate(offer))


@router.put(
    "/{id}",
    response_model=SuccessResponse[OfferResponse],
    summary="Update offer details (Admin/Owner)",
)
async def update_offer(
    id: int,
    req: OfferUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = OfferService(db)
    updated = await service.update_offer(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=OfferResponse.model_validate(updated))


@router.delete(
    "/{id}",
    response_model=MessageResponse,
    summary="Delete promotional offer (Admin/Owner)",
)
async def delete_offer(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = OfferService(db)
    await service.delete_offer(id, user_id=current_user.id, ip_address=ip_address)
    return MessageResponse(success=True, message=f"Offer #{id} deleted successfully.")
