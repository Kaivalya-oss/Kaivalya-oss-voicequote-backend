from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy import select, func, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.vehicle import VehicleBrand, VehicleModel, VehicleVariant, Accessory
from app.repositories.base_repository import BaseRepository


class VehicleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.brand_repo = BaseRepository[VehicleBrand](VehicleBrand, session)
        self.model_repo = BaseRepository[VehicleModel](VehicleModel, session)
        self.variant_repo = BaseRepository[VehicleVariant](VehicleVariant, session)
        self.accessory_repo = BaseRepository[Accessory](Accessory, session)

    # Brands
    async def get_brand_by_id(self, brand_id: int) -> Optional[VehicleBrand]:
        return await self.brand_repo.get_by_id(brand_id)

    async def get_brand_by_name(self, name: str) -> Optional[VehicleBrand]:
        stmt = select(VehicleBrand).where(VehicleBrand.name.ilike(name.strip()))
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_brands(self, active_only: bool = True) -> List[VehicleBrand]:
        stmt = select(VehicleBrand)
        if active_only:
            stmt = stmt.where(VehicleBrand.is_active.is_(True))
        stmt = stmt.order_by(VehicleBrand.name.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_brand(self, brand: VehicleBrand) -> VehicleBrand:
        return await self.brand_repo.create(brand)

    # Models
    async def get_model_by_id(self, model_id: int) -> Optional[VehicleModel]:
        stmt = (
            select(VehicleModel)
            .where(VehicleModel.id == model_id)
            .options(selectinload(VehicleModel.brand))
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_models(self, brand_id: Optional[int] = None, active_only: bool = True) -> List[VehicleModel]:
        stmt = select(VehicleModel).options(selectinload(VehicleModel.brand))
        if brand_id:
            stmt = stmt.where(VehicleModel.brand_id == brand_id)
        if active_only:
            stmt = stmt.where(VehicleModel.is_active.is_(True))
        stmt = stmt.order_by(VehicleModel.name.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_model(self, model: VehicleModel) -> VehicleModel:
        return await self.model_repo.create(model)

    # Variants
    async def get_variant_by_id(self, variant_id: int) -> Optional[VehicleVariant]:
        stmt = (
            select(VehicleVariant)
            .where(VehicleVariant.id == variant_id)
            .options(
                selectinload(VehicleVariant.model).selectinload(VehicleModel.brand)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_variant_by_code(self, code: str) -> Optional[VehicleVariant]:
        stmt = select(VehicleVariant).where(VehicleVariant.code == code.strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def search_variants(
        self,
        brand_id: Optional[int] = None,
        model_id: Optional[int] = None,
        fuel_type: Optional[str] = None,
        min_price: Optional[Decimal] = None,
        max_price: Optional[Decimal] = None,
        status: Optional[str] = "ACTIVE",
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[VehicleVariant], int]:
        stmt = (
            select(VehicleVariant)
            .join(VehicleVariant.model)
            .options(selectinload(VehicleVariant.model).selectinload(VehicleModel.brand))
        )
        count_stmt = select(func.count(VehicleVariant.id)).join(VehicleVariant.model)

        filters = []
        if brand_id:
            filters.append(VehicleModel.brand_id == brand_id)
        if model_id:
            filters.append(VehicleVariant.model_id == model_id)
        if fuel_type:
            filters.append(VehicleVariant.fuel_type.ilike(fuel_type.strip()))
        if min_price is not None:
            filters.append(VehicleVariant.on_road_price >= min_price)
        if max_price is not None:
            filters.append(VehicleVariant.on_road_price <= max_price)
        if status:
            filters.append(VehicleVariant.status == status.upper().strip())
        if search:
            filters.append(VehicleVariant.name.ilike(f"%{search.strip()}%"))

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        stmt = stmt.order_by(VehicleVariant.on_road_price.asc()).offset(skip).limit(limit)

        total = (await self.session.execute(count_stmt)).scalar_one()
        records = (await self.session.execute(stmt)).scalars().all()
        return list(records), total

    async def create_variant(self, variant: VehicleVariant) -> VehicleVariant:
        return await self.variant_repo.create(variant)

    async def update_variant(self, variant: VehicleVariant) -> VehicleVariant:
        return await self.variant_repo.update(variant)

    async def delete_variant(self, variant: VehicleVariant) -> None:
        await self.variant_repo.delete(variant)

    # Accessories
    async def list_accessories(self, active_only: bool = True) -> List[Accessory]:
        stmt = select(Accessory)
        if active_only:
            stmt = stmt.where(Accessory.is_active.is_(True))
        stmt = stmt.order_by(Accessory.name.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_accessory_by_id(self, acc_id: int) -> Optional[Accessory]:
        return await self.accessory_repo.get_by_id(acc_id)

    async def create_accessory(self, acc: Accessory) -> Accessory:
        return await self.accessory_repo.create(acc)
