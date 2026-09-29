from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inventory import InventoryUnit
from app.models.vehicle import VehicleVariant, VehicleModel, VehicleBrand
from app.repositories.base_repository import BaseRepository


class InventoryRepository(BaseRepository[InventoryUnit]):
    def __init__(self, session: AsyncSession):
        super().__init__(InventoryUnit, session)

    async def get_by_id(self, unit_id: int) -> Optional[InventoryUnit]:
        stmt = (
            select(InventoryUnit)
            .where(InventoryUnit.id == unit_id)
            .options(
                selectinload(InventoryUnit.variant)
                .selectinload(VehicleVariant.model)
                .selectinload(VehicleModel.brand)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_id_for_update(self, unit_id: int) -> Optional[InventoryUnit]:
        """Locks row with SELECT ... FOR UPDATE to eliminate concurrency race conditions."""
        stmt = (
            select(InventoryUnit)
            .where(InventoryUnit.id == unit_id)
            .with_for_update()
            .options(
                selectinload(InventoryUnit.variant)
                .selectinload(VehicleVariant.model)
                .selectinload(VehicleModel.brand)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_vin(self, vin: str) -> Optional[InventoryUnit]:
        stmt = select(InventoryUnit).where(InventoryUnit.vin_chassis_number == vin.strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_engine(self, engine: str) -> Optional[InventoryUnit]:
        stmt = select(InventoryUnit).where(InventoryUnit.engine_number == engine.strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def search_units(
        self,
        variant_id: Optional[int] = None,
        status: Optional[str] = None,
        branch_name: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[InventoryUnit], int]:
        stmt = (
            select(InventoryUnit)
            .options(
                selectinload(InventoryUnit.variant)
                .selectinload(VehicleVariant.model)
                .selectinload(VehicleModel.brand)
            )
        )
        count_stmt = select(func.count(InventoryUnit.id))

        filters = []
        if variant_id:
            filters.append(InventoryUnit.variant_id == variant_id)
        if status:
            filters.append(InventoryUnit.status == status.upper().strip())
        if branch_name:
            filters.append(InventoryUnit.branch_name.ilike(f"%{branch_name.strip()}%"))
        if search:
            s = f"%{search.strip()}%"
            filters.append(
                or_(
                    InventoryUnit.vin_chassis_number.ilike(s),
                    InventoryUnit.engine_number.ilike(s),
                    InventoryUnit.color.ilike(s),
                )
            )

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        stmt = stmt.order_by(InventoryUnit.id.desc()).offset(skip).limit(limit)

        total = (await self.session.execute(count_stmt)).scalar_one()
        records = (await self.session.execute(stmt)).scalars().all()
        return list(records), total

    async def get_stock_counts_by_variant(self, variant_id: int) -> Dict[str, int]:
        stmt = (
            select(InventoryUnit.status, func.count(InventoryUnit.id))
            .where(InventoryUnit.variant_id == variant_id)
            .group_by(InventoryUnit.status)
        )
        rows = (await self.session.execute(stmt)).all()
        counts = {
            "AVAILABLE": 0,
            "RESERVED": 0,
            "SOLD": 0,
            "SERVICE": 0,
            "IN_TRANSIT": 0,
            "TOTAL": 0,
        }
        for st, c in rows:
            counts[st] = c
            counts["TOTAL"] += c
        return counts
