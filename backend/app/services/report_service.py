from datetime import datetime
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.quotation import Quotation
from app.models.lead import Lead
from app.models.inventory import InventoryUnit
from app.models.vehicle import VehicleVariant
from app.schemas.report import InventoryReportItem, LeadReportItem, SalesReportItem


class ReportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_sales_report(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[SalesReportItem]:
        stmt = (
            select(Quotation)
            .where(Quotation.status == "ACCEPTED")
            .options(
                selectinload(Quotation.customer),
                selectinload(Quotation.variant),
                selectinload(Quotation.sales_executive),
            )
        )
        if from_date:
            stmt = stmt.where(Quotation.created_at >= from_date)
        if to_date:
            stmt = stmt.where(Quotation.created_at <= to_date)

        stmt = stmt.order_by(Quotation.created_at.desc()).limit(limit)
        quotes = (await self.session.execute(stmt)).scalars().all()

        return [
            SalesReportItem(
                quotation_id=q.id,
                quotation_number=q.quotation_number,
                customer_name=q.customer.full_name,
                variant_name=q.variant.name,
                sales_executive_name=q.sales_executive.full_name,
                final_price=q.final_price,
                status=q.status,
                created_at=q.created_at,
            )
            for q in quotes
        ]

    async def get_leads_report(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[LeadReportItem]:
        stmt = (
            select(Lead)
            .options(
                selectinload(Lead.customer),
                selectinload(Lead.assigned_salesperson),
                selectinload(Lead.interested_variant),
            )
        )
        if from_date:
            stmt = stmt.where(Lead.created_at >= from_date)
        if to_date:
            stmt = stmt.where(Lead.created_at <= to_date)

        stmt = stmt.order_by(Lead.created_at.desc()).limit(limit)
        leads = (await self.session.execute(stmt)).scalars().all()

        return [
            LeadReportItem(
                lead_id=l.id,
                customer_name=l.customer.full_name,
                phone=l.customer.phone,
                status=l.status,
                priority=l.priority,
                source=l.source,
                assigned_salesperson_name=l.assigned_salesperson.full_name if l.assigned_salesperson else "Unassigned",
                interested_vehicle=l.interested_variant.name if l.interested_variant else None,
                created_at=l.created_at,
            )
            for l in leads
        ]

    async def get_inventory_report(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[InventoryReportItem]:
        stmt = (
            select(InventoryUnit)
            .options(selectinload(InventoryUnit.variant))
        )
        if status:
            stmt = stmt.where(InventoryUnit.status == status.upper().strip())

        stmt = stmt.order_by(InventoryUnit.id.desc()).limit(limit)
        units = (await self.session.execute(stmt)).scalars().all()

        return [
            InventoryReportItem(
                unit_id=u.id,
                vin_chassis_number=u.vin_chassis_number,
                engine_number=u.engine_number,
                variant_name=u.variant.name,
                color=u.color,
                status=u.status,
                branch_name=u.branch_name,
                on_road_price=u.variant.on_road_price,
            )
            for u in units
        ]
