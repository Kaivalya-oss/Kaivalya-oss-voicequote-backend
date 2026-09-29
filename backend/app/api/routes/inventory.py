import math
from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import INVENTORY_ROLES, ALL_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.inventory import (
    InventoryUnitCreate,
    InventoryUnitResponse,
    InventoryUnitUpdate,
    StockActionRequest,
    VariantStockSummary,
)
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get(
    "",
    response_model=PaginatedResponse[InventoryUnitResponse],
    summary="List and filter physical inventory units",
)
async def list_inventory_units(
    variant_id: Optional[int] = Query(None, description="Filter by vehicle variant ID"),
    status: Optional[str] = Query(None, description="Filter by unit status (AVAILABLE, RESERVED, SOLD, SERVICE, IN_TRANSIT)"),
    branch_name: Optional[str] = Query(None, description="Filter by showroom/branch name"),
    search: Optional[str] = Query(None, description="Search by VIN, engine number or color"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = InventoryService(db)
    units, total = await service.list_units(
        variant_id=variant_id,
        status=status,
        branch_name=branch_name,
        search=search,
        page=page,
        page_size=page_size,
    )
    pages = math.ceil(total / page_size) if total > 0 else 1
    return PaginatedResponse(
        items=[InventoryUnitResponse.model_validate(u) for u in units],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/summary/{variant_id}",
    response_model=SuccessResponse[VariantStockSummary],
    summary="Get stock availability summary for a vehicle variant",
)
async def get_variant_stock_summary(
    variant_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = InventoryService(db)
    summary = await service.get_variant_stock_summary(variant_id)
    return SuccessResponse(data=summary)


@router.get(
    "/{id}",
    response_model=SuccessResponse[InventoryUnitResponse],
    summary="Get individual inventory unit details",
)
async def get_inventory_unit(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = InventoryService(db)
    unit = await service.get_unit(id)
    return SuccessResponse(data=InventoryUnitResponse.model_validate(unit))


@router.post(
    "",
    response_model=SuccessResponse[InventoryUnitResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add a new physical unit to inventory (Inventory Manager/Admin)",
)
async def create_inventory_unit(
    req: InventoryUnitCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = InventoryService(db)
    unit = await service.create_unit(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=InventoryUnitResponse.model_validate(unit))


@router.put(
    "/{id}",
    response_model=SuccessResponse[InventoryUnitResponse],
    summary="Update inventory unit details or status (Inventory Manager/Admin)",
)
async def update_inventory_unit(
    id: int,
    req: InventoryUnitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = InventoryService(db)
    updated = await service.update_unit(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=InventoryUnitResponse.model_validate(updated))


@router.post(
    "/{id}/reserve",
    response_model=SuccessResponse[InventoryUnitResponse],
    summary="Reserve an available unit (Atomic lock)",
)
async def reserve_inventory_unit(
    id: int,
    action: Optional[StockActionRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*ALL_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = InventoryService(db)
    notes = action.notes if action else None
    reserved = await service.reserve_unit(id, notes=notes, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=InventoryUnitResponse.model_validate(reserved))


@router.post(
    "/{id}/release",
    response_model=SuccessResponse[InventoryUnitResponse],
    summary="Release a reserved unit back to available stock",
)
async def release_inventory_unit(
    id: int,
    action: Optional[StockActionRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*ALL_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = InventoryService(db)
    notes = action.notes if action else None
    released = await service.release_unit(id, notes=notes, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=InventoryUnitResponse.model_validate(released))
