from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException, BadRequestException
from app.models.vehicle import VehicleBrand, VehicleModel, VehicleVariant, Accessory
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.vehicle import BrandCreate, ModelCreate, VariantCreate, VariantUpdate, AccessoryCreate


class VehicleService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.vehicle_repo = VehicleRepository(session)
        self.audit_repo = AuditLogRepository(session)

    # Brands
    async def create_brand(self, req: BrandCreate, user_id: Optional[int] = None, ip_address: Optional[str] = None) -> VehicleBrand:
        existing = await self.vehicle_repo.get_brand_by_name(req.name)
        if existing:
            raise ConflictException("BRAND_EXISTS", f"Brand with name '{req.name}' already exists.")
        brand = VehicleBrand(
            name=req.name.strip(),
            country=req.country,
            logo_url=req.logo_url,
            is_active=req.is_active,
        )
        created = await self.vehicle_repo.create_brand(brand)
        await self.audit_repo.log_action(user_id, "BRAND_CREATED", "VEHICLE_BRAND", created.id, new_value={"name": created.name}, ip_address=ip_address)
        return created

    async def list_brands(self, active_only: bool = True) -> List[VehicleBrand]:
        return await self.vehicle_repo.list_brands(active_only=active_only)

    # Models
    async def create_model(self, req: ModelCreate, user_id: Optional[int] = None, ip_address: Optional[str] = None) -> VehicleModel:
        brand = await self.vehicle_repo.get_brand_by_id(req.brand_id)
        if not brand:
            raise NotFoundException("VehicleBrand", req.brand_id)
        model = VehicleModel(
            brand_id=req.brand_id,
            name=req.name.strip(),
            vehicle_type=req.vehicle_type.upper().strip(),
            description=req.description,
            is_active=req.is_active,
        )
        created = await self.vehicle_repo.create_model(model)
        await self.audit_repo.log_action(user_id, "MODEL_CREATED", "VEHICLE_MODEL", created.id, new_value={"name": created.name, "brand_id": created.brand_id}, ip_address=ip_address)
        return await self.vehicle_repo.get_model_by_id(created.id)  # type: ignore

    async def list_models(self, brand_id: Optional[int] = None, active_only: bool = True) -> List[VehicleModel]:
        return await self.vehicle_repo.list_models(brand_id=brand_id, active_only=active_only)

    # Variants
    async def create_variant(self, req: VariantCreate, user_id: Optional[int] = None, ip_address: Optional[str] = None) -> VehicleVariant:
        model = await self.vehicle_repo.get_model_by_id(req.model_id)
        if not model:
            raise NotFoundException("VehicleModel", req.model_id)

        existing_code = await self.vehicle_repo.get_variant_by_code(req.code)
        if existing_code:
            raise ConflictException("VARIANT_CODE_EXISTS", f"Variant with code '{req.code}' already exists.")

        # Financial on-road price calculation
        on_road = req.ex_showroom_price + req.insurance_price + req.rto_price + req.accessories_price

        variant = VehicleVariant(
            model_id=req.model_id,
            name=req.name.strip(),
            code=req.code.upper().strip(),
            engine_cc=req.engine_cc,
            fuel_type=req.fuel_type.upper().strip(),
            transmission=req.transmission.upper().strip(),
            ex_showroom_price=req.ex_showroom_price,
            insurance_price=req.insurance_price,
            rto_price=req.rto_price,
            accessories_price=req.accessories_price,
            on_road_price=on_road,
            color_options=req.color_options,
            status=req.status.upper().strip(),
            specs_json=req.specs_json,
        )
        created = await self.vehicle_repo.create_variant(variant)
        await self.audit_repo.log_action(
            user_id, "VARIANT_CREATED", "VEHICLE_VARIANT", created.id,
            new_value={"name": created.name, "code": created.code, "on_road_price": str(on_road)},
            ip_address=ip_address,
        )
        return await self.vehicle_repo.get_variant_by_id(created.id)  # type: ignore

    async def get_variant(self, variant_id: int) -> VehicleVariant:
        variant = await self.vehicle_repo.get_variant_by_id(variant_id)
        if not variant:
            raise NotFoundException("VehicleVariant", variant_id)
        return variant

    async def list_variants(
        self,
        brand_id: Optional[int] = None,
        model_id: Optional[int] = None,
        fuel_type: Optional[str] = None,
        min_price: Optional[Decimal] = None,
        max_price: Optional[Decimal] = None,
        status: Optional[str] = "ACTIVE",
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[VehicleVariant], int]:
        skip = (page - 1) * page_size
        return await self.vehicle_repo.search_variants(
            brand_id=brand_id,
            model_id=model_id,
            fuel_type=fuel_type,
            min_price=min_price,
            max_price=max_price,
            status=status,
            search=search,
            skip=skip,
            limit=page_size,
        )

    async def update_variant(
        self,
        variant_id: int,
        req: VariantUpdate,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
    ) -> VehicleVariant:
        variant = await self.get_variant(variant_id)

        if req.name is not None:
            variant.name = req.name.strip()
        if req.engine_cc is not None:
            variant.engine_cc = req.engine_cc
        if req.fuel_type is not None:
            variant.fuel_type = req.fuel_type.upper().strip()
        if req.transmission is not None:
            variant.transmission = req.transmission.upper().strip()
        if req.ex_showroom_price is not None:
            variant.ex_showroom_price = req.ex_showroom_price
        if req.insurance_price is not None:
            variant.insurance_price = req.insurance_price
        if req.rto_price is not None:
            variant.rto_price = req.rto_price
        if req.accessories_price is not None:
            variant.accessories_price = req.accessories_price
        if req.color_options is not None:
            variant.color_options = req.color_options
        if req.status is not None:
            variant.status = req.status.upper().strip()
        if req.specs_json is not None:
            variant.specs_json = req.specs_json

        # Recalculate on-road price
        variant.on_road_price = (
            variant.ex_showroom_price
            + variant.insurance_price
            + variant.rto_price
            + variant.accessories_price
        )

        updated = await self.vehicle_repo.update_variant(variant)
        await self.audit_repo.log_action(
            user_id, "VARIANT_UPDATED", "VEHICLE_VARIANT", updated.id,
            new_value={"name": updated.name, "on_road_price": str(updated.on_road_price)},
            ip_address=ip_address,
        )
        return await self.vehicle_repo.get_variant_by_id(updated.id)  # type: ignore

    async def delete_variant(self, variant_id: int, user_id: Optional[int] = None, ip_address: Optional[str] = None) -> None:
        variant = await self.get_variant(variant_id)
        await self.vehicle_repo.delete_variant(variant)
        await self.audit_repo.log_action(user_id, "VARIANT_DELETED", "VEHICLE_VARIANT", variant_id, ip_address=ip_address)

    # Accessories
    async def create_accessory(self, req: AccessoryCreate, user_id: Optional[int] = None, ip_address: Optional[str] = None) -> Accessory:
        acc = Accessory(
            name=req.name.strip(),
            part_number=req.part_number,
            price=req.price,
            description=req.description,
            is_active=req.is_active,
        )
        created = await self.vehicle_repo.create_accessory(acc)
        await self.audit_repo.log_action(user_id, "ACCESSORY_CREATED", "ACCESSORY", created.id, ip_address=ip_address)
        return created

    async def list_accessories(self, active_only: bool = True) -> List[Accessory]:
        return await self.vehicle_repo.list_accessories(active_only=active_only)
