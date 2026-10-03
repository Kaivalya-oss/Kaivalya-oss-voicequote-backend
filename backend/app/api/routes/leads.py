import math
from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import MANAGEMENT_ROLES, SALES_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from app.schemas.lead import LeadCreate, LeadResponse, LeadStatusTransitionRequest, LeadUpdate
from app.services.access_policy import (
    ensure_can_assign_lead,
    ensure_can_manage_lead,
    ensure_can_view_lead,
    is_scoped_reader,
)
from app.services.lead_service import LeadService

router = APIRouter(prefix="/leads", tags=["Leads"])


@router.post(
    "",
    response_model=SuccessResponse[LeadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new sales lead",
)
async def create_lead(
    req: LeadCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    ensure_can_assign_lead(current_user, req.assigned_salesperson_id)
    service = LeadService(db)
    lead = await service.create_lead(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=LeadResponse.model_validate(lead))


@router.get(
    "",
    response_model=PaginatedResponse[LeadResponse],
    summary="List, search, and filter leads",
)
async def list_leads(
    customer_id: Optional[int] = Query(None, description="Filter by customer ID"),
    assigned_salesperson_id: Optional[int] = Query(None, description="Filter by assigned salesperson ID"),
    status: Optional[str] = Query(None, description="Filter by lead status (NEW, CONTACTED, INTERESTED, TEST_RIDE, NEGOTIATION, BOOKED, PURCHASED, LOST)"),
    priority: Optional[str] = Query(None, description="Filter by priority (LOW, MEDIUM, HIGH, URGENT)"),
    source: Optional[str] = Query(None, description="Filter by source (WALK_IN, ONLINE, REFERRAL, PHONE_INQUIRY, CAMPAIGN)"),
    search: Optional[str] = Query(None, description="Search customer name or notes"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = LeadService(db)
    leads, total = await service.list_leads(
        customer_id=customer_id,
        assigned_salesperson_id=assigned_salesperson_id,
        status=status,
        priority=priority,
        source=source,
        search=search,
        page=page,
        page_size=page_size,
        visible_to_user_id=current_user.id if is_scoped_reader(current_user) else None,
    )
    pages = math.ceil(total / page_size) if total > 0 else 1
    return PaginatedResponse(
        items=[LeadResponse.model_validate(l) for l in leads],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{id}",
    response_model=SuccessResponse[LeadResponse],
    summary="Get lead details and status audit history",
)
async def get_lead(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = LeadService(db)
    lead = await service.get_lead(id)
    ensure_can_view_lead(current_user, lead)
    return SuccessResponse(data=LeadResponse.model_validate(lead))


@router.put(
    "/{id}",
    response_model=SuccessResponse[LeadResponse],
    summary="Update lead metadata or reassign salesperson",
)
async def update_lead(
    id: int,
    req: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = LeadService(db)
    ensure_can_manage_lead(current_user, await service.get_lead(id))
    if req.assigned_salesperson_id is not None:
        ensure_can_assign_lead(current_user, req.assigned_salesperson_id)
    updated = await service.update_lead(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=LeadResponse.model_validate(updated))


@router.post(
    "/{id}/status",
    response_model=SuccessResponse[LeadResponse],
    summary="Transition lead to a new state in the sales pipeline",
)
async def transition_lead_status(
    id: int,
    req: LeadStatusTransitionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = LeadService(db)
    ensure_can_manage_lead(current_user, await service.get_lead(id))
    updated = await service.transition_status(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=LeadResponse.model_validate(updated))


@router.delete(
    "/{id}",
    response_model=MessageResponse,
    summary="Delete lead record (Admin/Owner)",
)
async def delete_lead(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = LeadService(db)
    await service.delete_lead(id, user_id=current_user.id, ip_address=ip_address)
    return MessageResponse(success=True, message=f"Lead #{id} deleted successfully.")
