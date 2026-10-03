from datetime import datetime
import math
from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import SALES_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.followup import (
    FollowupCancelRequest,
    FollowupCompleteRequest,
    FollowupCreate,
    FollowupResponse,
    FollowupUpdate,
)
from app.repositories.lead_repository import LeadRepository
from app.services.access_policy import (
    ensure_can_manage_followup,
    ensure_can_manage_lead,
    ensure_can_view_followup,
    is_scoped_reader,
)
from app.services.followup_service import FollowupService

router = APIRouter(prefix="/followups", tags=["Follow-ups"])


async def _ensure_followup_access(db: AsyncSession, service: FollowupService, followup_id: int, user: User) -> None:
    followup = await service.get_followup(followup_id)
    lead = await LeadRepository(db).get_by_id(followup.lead_id)
    ensure_can_manage_followup(user, followup, lead)


@router.post(
    "",
    response_model=SuccessResponse[FollowupResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a sales follow-up task",
)
async def create_followup(
    req: FollowupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    lead = await LeadRepository(db).get_by_id(req.lead_id)
    if lead is not None:
        ensure_can_manage_lead(current_user, lead)
    service = FollowupService(db)
    followup = await service.create_followup(req, assigned_user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=FollowupResponse.model_validate(followup))


@router.get(
    "",
    response_model=PaginatedResponse[FollowupResponse],
    summary="List and filter scheduled follow-ups",
)
async def list_followups(
    lead_id: Optional[int] = Query(None, description="Filter by lead ID"),
    assigned_user_id: Optional[int] = Query(None, description="Filter by assigned user ID"),
    status: Optional[str] = Query(None, description="Filter by status (SCHEDULED, COMPLETED, CANCELLED)"),
    from_date: Optional[datetime] = Query(None, description="From scheduled date"),
    to_date: Optional[datetime] = Query(None, description="To scheduled date"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = FollowupService(db)
    followups, total = await service.list_followups(
        lead_id=lead_id,
        assigned_user_id=assigned_user_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        page=page,
        page_size=page_size,
        visible_to_user_id=current_user.id if is_scoped_reader(current_user) else None,
    )
    pages = math.ceil(total / page_size) if total > 0 else 1
    return PaginatedResponse(
        items=[FollowupResponse.model_validate(f) for f in followups],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{id}",
    response_model=SuccessResponse[FollowupResponse],
    summary="Get follow-up details",
)
async def get_followup(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = FollowupService(db)
    followup = await service.get_followup(id)
    ensure_can_view_followup(current_user, followup, await LeadRepository(db).get_by_id(followup.lead_id))
    return SuccessResponse(data=FollowupResponse.model_validate(followup))


@router.put(
    "/{id}",
    response_model=SuccessResponse[FollowupResponse],
    summary="Reschedule or update follow-up",
)
async def update_followup(
    id: int,
    req: FollowupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = FollowupService(db)
    await _ensure_followup_access(db, service, id, current_user)
    updated = await service.update_followup(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=FollowupResponse.model_validate(updated))


@router.post(
    "/{id}/complete",
    response_model=SuccessResponse[FollowupResponse],
    summary="Mark follow-up as completed with outcome notes",
)
async def complete_followup(
    id: int,
    req: FollowupCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = FollowupService(db)
    await _ensure_followup_access(db, service, id, current_user)
    completed = await service.complete_followup(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=FollowupResponse.model_validate(completed))


@router.post(
    "/{id}/cancel",
    response_model=SuccessResponse[FollowupResponse],
    summary="Cancel a scheduled follow-up",
)
async def cancel_followup(
    id: int,
    req: FollowupCancelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = FollowupService(db)
    await _ensure_followup_access(db, service, id, current_user)
    cancelled = await service.cancel_followup(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=FollowupResponse.model_validate(cancelled))
