import math
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import SALES_ROLES, MANAGEMENT_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from app.schemas.customer import CustomerCreate, CustomerResponse, CustomerUpdate
from app.services.customer_service import CustomerService

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post(
    "",
    response_model=SuccessResponse[CustomerResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new customer profile",
)
async def create_customer(
    req: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = CustomerService(db)
    customer = await service.create_customer(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=CustomerResponse.model_validate(customer))


@router.get(
    "",
    response_model=PaginatedResponse[CustomerResponse],
    summary="List, search and filter customers",
)
async def list_customers(
    search: Optional[str] = Query(None, description="Search by name, phone or email"),
    city: Optional[str] = Query(None, description="Filter by city"),
    min_budget: Optional[Decimal] = Query(None, description="Minimum customer budget"),
    max_budget: Optional[Decimal] = Query(None, description="Maximum customer budget"),
    sort_by: str = Query("id", description="Field to sort by (id, full_name, budget, created_at)"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = CustomerService(db)
    customers, total = await service.list_customers(
        search=search,
        city=city,
        min_budget=min_budget,
        max_budget=max_budget,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    pages = math.ceil(total / page_size) if total > 0 else 1
    return PaginatedResponse(
        items=[CustomerResponse.model_validate(c) for c in customers],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{id}",
    response_model=SuccessResponse[CustomerResponse],
    summary="Retrieve customer details by ID",
)
async def get_customer(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = CustomerService(db)
    customer = await service.get_customer(id)
    return SuccessResponse(data=CustomerResponse.model_validate(customer))


@router.put(
    "/{id}",
    response_model=SuccessResponse[CustomerResponse],
    summary="Update customer profile",
)
async def update_customer(
    id: int,
    req: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = CustomerService(db)
    updated = await service.update_customer(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=CustomerResponse.model_validate(updated))


@router.delete(
    "/{id}",
    response_model=MessageResponse,
    summary="Delete customer profile",
)
async def delete_customer(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = CustomerService(db)
    await service.delete_customer(id, user_id=current_user.id, ip_address=ip_address)
    return MessageResponse(success=True, message=f"Customer #{id} successfully deleted.")
