from datetime import datetime
import math
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import SALES_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.test_ride import (
    TestRideCompleteRequest,
    TestRideCreate,
    TestRideResponse,
)
from app.services.test_ride_service import TestRideService

router = APIRouter(prefix="/test-rides", tags=["Test Rides"])


class CancelRideRequest(BaseModel):
    reason: Optional[str] = None


@router.post(
    "",
    response_model=SuccessResponse[TestRideResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Book a customer vehicle test ride (with collision check)",
)
async def book_test_ride(
    req: TestRideCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = TestRideService(db)
    ride = await service.book_test_ride(req, sales_executive_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=TestRideResponse.model_validate(ride))


@router.get(
    "",
    response_model=PaginatedResponse[TestRideResponse],
    summary="List and filter booked test rides",
)
async def list_test_rides(
    customer_id: Optional[int] = Query(None, description="Filter by customer ID"),
    variant_id: Optional[int] = Query(None, description="Filter by variant ID"),
    sales_executive_id: Optional[int] = Query(None, description="Filter by sales executive ID"),
    status: Optional[str] = Query(None, description="Filter by status (REQUESTED, CONFIRMED, COMPLETED, CANCELLED, NO_SHOW)"),
    from_date: Optional[datetime] = Query(None, description="From scheduled time"),
    to_date: Optional[datetime] = Query(None, description="To scheduled time"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = TestRideService(db)
    rides, total = await service.list_test_rides(
        customer_id=customer_id,
        variant_id=variant_id,
        sales_executive_id=sales_executive_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        page=page,
        page_size=page_size,
    )
    pages = math.ceil(total / page_size) if total > 0 else 1
    return PaginatedResponse(
        items=[TestRideResponse.model_validate(r) for r in rides],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{id}",
    response_model=SuccessResponse[TestRideResponse],
    summary="Get test ride booking details",
)
async def get_test_ride(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = TestRideService(db)
    ride = await service.get_test_ride(id)
    return SuccessResponse(data=TestRideResponse.model_validate(ride))


@router.post(
    "/{id}/confirm",
    response_model=SuccessResponse[TestRideResponse],
    summary="Confirm a requested test ride booking",
)
async def confirm_test_ride(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = TestRideService(db)
    confirmed = await service.confirm_test_ride(id, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=TestRideResponse.model_validate(confirmed))


@router.post(
    "/{id}/complete",
    response_model=SuccessResponse[TestRideResponse],
    summary="Record customer rating and complete test ride",
)
async def complete_test_ride(
    id: int,
    req: TestRideCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = TestRideService(db)
    completed = await service.complete_test_ride(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=TestRideResponse.model_validate(completed))


@router.post(
    "/{id}/cancel",
    response_model=SuccessResponse[TestRideResponse],
    summary="Cancel a booked test ride",
)
async def cancel_test_ride(
    id: int,
    req: Optional[CancelRideRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = TestRideService(db)
    reason = req.reason if req else None
    cancelled = await service.cancel_test_ride(id, reason=reason, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=TestRideResponse.model_validate(cancelled))
