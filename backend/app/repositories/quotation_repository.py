from datetime import datetime, timezone
import random
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.quotation import Quotation, QuotationItem
from app.models.vehicle import VehicleVariant, VehicleModel, VehicleBrand
from app.repositories.base_repository import BaseRepository


class QuotationRepository(BaseRepository[Quotation]):
    def __init__(self, session: AsyncSession):
        super().__init__(Quotation, session)

    async def get_by_id(self, quotation_id: int) -> Optional[Quotation]:
        stmt = (
            select(Quotation)
            .where(Quotation.id == quotation_id)
            .options(
                selectinload(Quotation.customer),
                selectinload(Quotation.sales_executive),
                selectinload(Quotation.variant).selectinload(VehicleVariant.model).selectinload(VehicleModel.brand),
                selectinload(Quotation.offer),
                selectinload(Quotation.items),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_quotation_number(self, q_num: str) -> Optional[Quotation]:
        stmt = (
            select(Quotation)
            .where(Quotation.quotation_number == q_num.strip())
            .options(
                selectinload(Quotation.customer),
                selectinload(Quotation.sales_executive),
                selectinload(Quotation.variant).selectinload(VehicleVariant.model).selectinload(VehicleModel.brand),
                selectinload(Quotation.offer),
                selectinload(Quotation.items),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def generate_unique_quotation_number(self) -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        for _ in range(20):
            suffix = f"{random.randint(1000, 9999)}"
            q_num = f"QT-{date_str}-{suffix}"
            existing = await self.get_by_quotation_number(q_num)
            if not existing:
                return q_num
        # Fallback with timestamp seconds
        return f"QT-{date_str}-{int(datetime.now().timestamp()) % 10000:04d}"

    async def search_quotations(
        self,
        customer_id: Optional[int] = None,
        sales_executive_id: Optional[int] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Quotation], int]:
        stmt = (
            select(Quotation)
            .options(
                selectinload(Quotation.customer),
                selectinload(Quotation.sales_executive),
                selectinload(Quotation.variant).selectinload(VehicleVariant.model).selectinload(VehicleModel.brand),
                selectinload(Quotation.offer),
                selectinload(Quotation.items),
            )
        )
        count_stmt = select(func.count(Quotation.id))

        filters = []
        if customer_id:
            filters.append(Quotation.customer_id == customer_id)
        if sales_executive_id:
            filters.append(Quotation.sales_executive_id == sales_executive_id)
        if status:
            filters.append(Quotation.status == status.upper().strip())
        if from_date:
            filters.append(Quotation.created_at >= from_date)
        if to_date:
            filters.append(Quotation.created_at <= to_date)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        stmt = stmt.order_by(Quotation.id.desc()).offset(skip).limit(limit)

        total = (await self.session.execute(count_stmt)).scalar_one()
        records = (await self.session.execute(stmt)).scalars().all()
        return list(records), total
