from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.test_ride import TestRide
from app.models.vehicle import VehicleVariant, VehicleModel, VehicleBrand
from app.repositories.base_repository import BaseRepository


class TestRideRepository(BaseRepository[TestRide]):
    def __init__(self, session: AsyncSession):
        super().__init__(TestRide, session)

    async def get_by_id(self, ride_id: int) -> Optional[TestRide]:
        stmt = (
            select(TestRide)
            .where(TestRide.id == ride_id)
            .options(
                selectinload(TestRide.customer),
                selectinload(TestRide.sales_executive),
                selectinload(TestRide.variant).selectinload(VehicleVariant.model).selectinload(VehicleModel.brand),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def check_collision(
        self,
        variant_id: int,
        scheduled_at: datetime,
        duration_minutes: int,
        exclude_id: Optional[int] = None,
    ) -> bool:
        """
        Check if any active test ride exists for the same vehicle variant in an overlapping window.
        """
        window_start = scheduled_at - timedelta(minutes=duration_minutes)
        window_end = scheduled_at + timedelta(minutes=duration_minutes)

        stmt = select(TestRide).where(
            TestRide.variant_id == variant_id,
            TestRide.status.in_(["REQUESTED", "CONFIRMED"]),
            TestRide.scheduled_at >= window_start,
            TestRide.scheduled_at <= window_end,
        )
        if exclude_id:
            stmt = stmt.where(TestRide.id != exclude_id)

        result = await self.session.execute(stmt)
        return result.scalars().first() is not None

    async def search_test_rides(
        self,
        customer_id: Optional[int] = None,
        variant_id: Optional[int] = None,
        sales_executive_id: Optional[int] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[TestRide], int]:
        stmt = (
            select(TestRide)
            .options(
                selectinload(TestRide.customer),
                selectinload(TestRide.sales_executive),
                selectinload(TestRide.variant).selectinload(VehicleVariant.model).selectinload(VehicleModel.brand),
            )
        )
        count_stmt = select(func.count(TestRide.id))

        filters = []
        if customer_id:
            filters.append(TestRide.customer_id == customer_id)
        if variant_id:
            filters.append(TestRide.variant_id == variant_id)
        if sales_executive_id:
            filters.append(TestRide.sales_executive_id == sales_executive_id)
        if status:
            filters.append(TestRide.status == status.upper().strip())
        if from_date:
            filters.append(TestRide.scheduled_at >= from_date)
        if to_date:
            filters.append(TestRide.scheduled_at <= to_date)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        stmt = stmt.order_by(TestRide.scheduled_at.desc()).offset(skip).limit(limit)

        total = (await self.session.execute(count_stmt)).scalar_one()
        records = (await self.session.execute(stmt)).scalars().all()
        return list(records), total
