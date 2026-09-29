from datetime import datetime
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.followup import Followup
from app.models.lead import Lead
from app.models.customer import Customer
from app.repositories.base_repository import BaseRepository


class FollowupRepository(BaseRepository[Followup]):
    def __init__(self, session: AsyncSession):
        super().__init__(Followup, session)

    async def get_by_id(self, followup_id: int) -> Optional[Followup]:
        stmt = (
            select(Followup)
            .where(Followup.id == followup_id)
            .options(
                selectinload(Followup.assigned_user),
                selectinload(Followup.lead).selectinload(Lead.customer),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def search_followups(
        self,
        lead_id: Optional[int] = None,
        assigned_user_id: Optional[int] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Followup], int]:
        stmt = (
            select(Followup)
            .options(
                selectinload(Followup.assigned_user),
                selectinload(Followup.lead).selectinload(Lead.customer),
            )
        )
        count_stmt = select(func.count(Followup.id))

        filters = []
        if lead_id:
            filters.append(Followup.lead_id == lead_id)
        if assigned_user_id:
            filters.append(Followup.assigned_user_id == assigned_user_id)
        if status:
            filters.append(Followup.status == status.upper().strip())
        if from_date:
            filters.append(Followup.scheduled_at >= from_date)
        if to_date:
            filters.append(Followup.scheduled_at <= to_date)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        stmt = stmt.order_by(Followup.scheduled_at.asc()).offset(skip).limit(limit)

        total = (await self.session.execute(count_stmt)).scalar_one()
        records = (await self.session.execute(stmt)).scalars().all()
        return list(records), total

    async def get_due_followups(self, cutoff_time: datetime) -> List[Followup]:
        """Find pending scheduled followups due up to cutoff_time."""
        stmt = (
            select(Followup)
            .where(
                Followup.status == "SCHEDULED",
                Followup.scheduled_at <= cutoff_time,
            )
            .options(
                selectinload(Followup.assigned_user),
                selectinload(Followup.lead).selectinload(Lead.customer),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
