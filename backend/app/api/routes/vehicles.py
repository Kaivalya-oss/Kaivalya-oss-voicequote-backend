import math
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.permissions import MANAGEMENT_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from app.schemas.vehicle import (
    AccessoryCreate, AccessoryResponse,
    BrandCreate, BrandResponse,
    ModelCreate, ModelResponse,
    VariantCreate, VariantResponse, VariantUpdate,
)
from app.services.vehicle_service import VehicleService

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])


# --- Catalog Lookups (Brands, Models, Accessories) ---

@router.get("/catalog/brands", response_model=SuccessResponse[List[BrandResponse]], summary="List all vehicle brands")
async def list_brands(db: AsyncSession = Depends(get_db)):
    service = VehicleService(db)
    brands = await service.list_brands()
    return SuccessResponse(data=[BrandResponse.model_validate(b) for b in brands])


@router.post(
    "/catalog/brands",
    response_model=SuccessResponse[BrandResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new vehicle brand (Admin/Owner)",
)
async def create_brand(
    req: BrandCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = VehicleService(db)
    brand = await service.create_brand(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=BrandResponse.model_validate(brand))


@router.get("/catalog/models", response_model=SuccessResponse[List[ModelResponse]], summary="List models (optionally by brand)")
async def list_models(
    brand_id: Optional[int] = Query(None, description="Filter by brand ID"),
    db: AsyncSession = Depends(get_db),
):
    service = VehicleService(db)
    models = await service.list_models(brand_id=brand_id)
    return SuccessResponse(data=[ModelResponse.model_validate(m) for m in models])


@router.post(
    "/catalog/models",
    response_model=SuccessResponse[ModelResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new vehicle model (Admin/Owner)",
)
async def create_model(
    req: ModelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = VehicleService(db)
    model = await service.create_model(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=ModelResponse.model_validate(model))


@router.get("/catalog/accessories", response_model=SuccessResponse[List[AccessoryResponse]], summary="List standard accessories")
async def list_accessories(db: AsyncSession = Depends(get_db)):
    service = VehicleService(db)
    accessories = await service.list_accessories()
    return SuccessResponse(data=[AccessoryResponse.model_validate(a) for a in accessories])


@router.post(
    "/catalog/accessories",
    response_model=SuccessResponse[AccessoryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a standard accessory (Admin/Owner)",
)
async def create_accessory(
    req: AccessoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = VehicleService(db)
    accessory = await service.create_accessory(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=AccessoryResponse.model_validate(accessory))


# --- Vehicle Variants CRUD ---

@router.get(
    "",
    response_model=PaginatedResponse[VariantResponse],
    summary="Search, filter, and list vehicle variants",
)
async def list_vehicles(
    brand_id: Optional[int] = Query(None, description="Filter by Brand ID"),
    model_id: Optional[int] = Query(None, description="Filter by Model ID"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type (PETROL, ELECTRIC, HYBRID)"),
    min_price: Optional[Decimal] = Query(None, description="Minimum on-road price"),
    max_price: Optional[Decimal] = Query(None, description="Maximum on-road price"),
    status: Optional[str] = Query("ACTIVE", description="Status (ACTIVE, DISCONTINUED, or all)"),
    search: Optional[str] = Query(None, description="Search variant name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    service = VehicleService(db)
    variants, total = await service.list_variants(
        brand_id=brand_id,
        model_id=model_id,
        fuel_type=fuel_type,
        min_price=min_price,
        max_price=max_price,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
    )
    pages = math.ceil(total / page_size) if total > 0 else 1
    return PaginatedResponse(
        items=[VariantResponse.model_validate(v) for v in variants],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{id}",
    response_model=SuccessResponse[VariantResponse],
    summary="Get vehicle variant details by ID",
)
async def get_vehicle(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    service = VehicleService(db)
    variant = await service.get_variant(id)
    return SuccessResponse(data=VariantResponse.model_validate(variant))


@router.post(
    "",
    response_model=SuccessResponse[VariantResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new vehicle variant (Admin/Owner)",
)
async def create_vehicle(
    req: VariantCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = VehicleService(db)
    variant = await service.create_variant(req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=VariantResponse.model_validate(variant))


@router.put(
    "/{id}",
    response_model=SuccessResponse[VariantResponse],
    summary="Update vehicle variant pricing and specifications (Admin/Owner)",
)
async def update_vehicle(
    id: int,
    req: VariantUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = VehicleService(db)
    updated = await service.update_variant(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=VariantResponse.model_validate(updated))


@router.delete(
    "/{id}",
    response_model=MessageResponse,
    summary="Delete vehicle variant (Admin/Owner)",
)
async def delete_vehicle(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = VehicleService(db)
    await service.delete_variant(id, user_id=current_user.id, ip_address=ip_address)
    return MessageResponse(success=True, message=f"Vehicle variant #{id} deleted successfully.")
