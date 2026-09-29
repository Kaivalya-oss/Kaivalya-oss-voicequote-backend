from typing import List, Optional, Tuple
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.lead import Lead, LeadStatusHistory
from app.models.customer import Customer
from app.models.user import User
from app.models.vehicle import VehicleVariant, VehicleModel, VehicleBrand
from app.repositories.base_repository import BaseRepository


class LeadRepository(BaseRepository[Lead]):
    def __init__(self, session: AsyncSession):
        super().__init__(Lead, session)

    async def get_by_id(self, lead_id: int) -> Optional[Lead]:
        stmt = (
            select(Lead)
            .where(Lead.id == lead_id)
            .options(
                selectinload(Lead.customer),
                selectinload(Lead.assigned_salesperson),
                selectinload(Lead.interested_variant).selectinload(VehicleVariant.model).selectinload(VehicleModel.brand),
                selectinload(Lead.status_history),
                selectinload(Lead.followups),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def search_leads(
        self,
        customer_id: Optional[int] = None,
        assigned_salesperson_id: Optional[int] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Lead], int]:
        stmt = (
            select(Lead)
            .join(Lead.customer)
            .options(
                selectinload(Lead.customer),
                selectinload(Lead.assigned_salesperson),
                selectinload(Lead.interested_variant).selectinload(VehicleVariant.model).selectinload(VehicleModel.brand),
                selectinload(Lead.status_history),
            )
        )
        count_stmt = select(func.count(Lead.id)).join(Lead.customer)

        filters = []
        if customer_id:
            filters.append(Lead.customer_id == customer_id)
        if assigned_salesperson_id:
            filters.append(Lead.assigned_salesperson_id == assigned_salesperson_id)
        if status:
            filters.append(Lead.status == status.upper().strip())
        if priority:
            filters.append(Lead.priority == priority.upper().strip())
        if source:
            filters.append(Lead.source == source.upper().strip())
        if search:
            s = f"%{search.strip()}%"
            filters.append(
                or_(
                    Customer.full_name.ilike(s),
                    Customer.phone.ilike(s),
                    Lead.notes.ilike(s),
                )
            )

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        stmt = stmt.order_by(Lead.id.desc()).offset(skip).limit(limit)

        total = (await self.session.execute(count_stmt)).scalar_one()
        records = (await self.session.execute(stmt)).scalars().all()
        return list(records), total

    async def add_status_history(
        self,
        lead_id: int,
        from_status: str,
        to_status: str,
        user_id: Optional[int],
        reason: Optional[str] = None,
    ) -> LeadStatusHistory:
        history = LeadStatusHistory(
            lead_id=lead_id,
            from_status=from_status,
            to_status=to_status,
            changed_by_user_id=user_id,
            reason=reason,
        )
        self.session.add(history)
        await self.session.commit()
        return history
