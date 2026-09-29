from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.lead import Lead
from app.models.quotation import Quotation
from app.models.inventory import InventoryUnit
from app.models.test_ride import TestRide
from app.models.followup import Followup
from app.models.vehicle import VehicleVariant, VehicleModel, VehicleBrand
from app.models.user import User
from app.schemas.dashboard import (
    DashboardSummary,
    SalesDashboardMetrics,
    LeadsDashboardMetrics,
    InventoryDashboardMetrics,
    RevenueDashboardMetrics,
)


class DashboardService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_summary(self) -> DashboardSummary:
        # Customers count
        cust_cnt = (await self.session.execute(select(func.count(Customer.id)))).scalar_one()

        # Leads count
        lead_cnt = (await self.session.execute(select(func.count(Lead.id)))).scalar_one()

        # Quotations generated & accepted
        q_gen = (await self.session.execute(select(func.count(Quotation.id)))).scalar_one()
        q_acc = (await self.session.execute(select(func.count(Quotation.id)).where(Quotation.status == "ACCEPTED"))).scalar_one()

        # Bookings & Purchases
        bookings = (await self.session.execute(select(func.count(Lead.id)).where(Lead.status == "BOOKED"))).scalar_one()
        purchases = (await self.session.execute(select(func.count(Lead.id)).where(Lead.status == "PURCHASED"))).scalar_one()

        # Revenue from accepted quotations
        rev_stmt = select(func.coalesce(func.sum(Quotation.final_price), Decimal("0.00"))).where(Quotation.status == "ACCEPTED")
        revenue = (await self.session.execute(rev_stmt)).scalar_one()

        # Followups pending
        pending_fups = (await self.session.execute(select(func.count(Followup.id)).where(Followup.status == "SCHEDULED"))).scalar_one()

        # Inventory
        avail_inv = (await self.session.execute(select(func.count(InventoryUnit.id)).where(InventoryUnit.status == "AVAILABLE"))).scalar_one()
        res_inv = (await self.session.execute(select(func.count(InventoryUnit.id)).where(InventoryUnit.status == "RESERVED"))).scalar_one()

        # Test rides
        rides_cnt = (await self.session.execute(select(func.count(TestRide.id)))).scalar_one()

        # Conversion rate
        conv_rate = Decimal("0.00")
        if lead_cnt > 0:
            conv_rate = (Decimal(purchases) / Decimal(lead_cnt) * Decimal("100.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return DashboardSummary(
            total_customers=cust_cnt,
            total_leads=lead_cnt,
            quotations_generated=q_gen,
            quotations_accepted=q_acc,
            active_bookings=bookings,
            completed_purchases=purchases,
            total_revenue=revenue,
            pending_followups=pending_fups,
            available_inventory=avail_inv,
            reserved_inventory=res_inv,
            total_test_rides=rides_cnt,
            lead_conversion_rate_percentage=conv_rate,
        )

    async def get_sales_metrics(self) -> SalesDashboardMetrics:
        total_q = (await self.session.execute(select(func.count(Quotation.id)))).scalar_one()
        acc_q = (await self.session.execute(select(func.count(Quotation.id)).where(Quotation.status == "ACCEPTED"))).scalar_one()
        rej_q = (await self.session.execute(select(func.count(Quotation.id)).where(Quotation.status == "REJECTED"))).scalar_one()

        conv = Decimal("0.00")
        if total_q > 0:
            conv = (Decimal(acc_q) / Decimal(total_q) * Decimal("100.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Sales by variant
        var_stmt = (
            select(VehicleVariant.name, func.count(Quotation.id))
            .join(Quotation, Quotation.variant_id == VehicleVariant.id)
            .group_by(VehicleVariant.name)
        )
        var_rows = (await self.session.execute(var_stmt)).all()
        by_variant = [{"variant": row[0], "count": row[1]} for row in var_rows]

        # Sales by sales executive
        exec_stmt = (
            select(User.full_name, func.count(Quotation.id))
            .join(Quotation, Quotation.sales_executive_id == User.id)
            .group_by(User.full_name)
        )
        exec_rows = (await self.session.execute(exec_stmt)).all()
        by_exec = [{"executive": row[0], "count": row[1]} for row in exec_rows]

        return SalesDashboardMetrics(
            total_quotations=total_q,
            accepted_quotations=acc_q,
            rejected_quotations=rej_q,
            conversion_rate_percentage=conv,
            sales_by_variant=by_variant,
            sales_by_executive=by_exec,
        )

    async def get_leads_metrics(self) -> LeadsDashboardMetrics:
        total_leads = (await self.session.execute(select(func.count(Lead.id)))).scalar_one()

        # By status
        status_rows = (await self.session.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status))).all()
        by_status = {row[0]: row[1] for row in status_rows}

        # By source
        source_rows = (await self.session.execute(select(Lead.source, func.count(Lead.id)).group_by(Lead.source))).all()
        by_source = {row[0]: row[1] for row in source_rows}

        # By priority
        priority_rows = (await self.session.execute(select(Lead.priority, func.count(Lead.id)).group_by(Lead.priority))).all()
        by_priority = {row[0]: row[1] for row in priority_rows}

        return LeadsDashboardMetrics(
            total_leads=total_leads,
            leads_by_status=by_status,
            leads_by_source=by_source,
            leads_by_priority=by_priority,
        )

    async def get_inventory_metrics(self) -> InventoryDashboardMetrics:
        total = (await self.session.execute(select(func.count(InventoryUnit.id)))).scalar_one()

        counts = {"AVAILABLE": 0, "RESERVED": 0, "SOLD": 0, "SERVICE": 0, "IN_TRANSIT": 0}
        rows = (await self.session.execute(select(InventoryUnit.status, func.count(InventoryUnit.id)).group_by(InventoryUnit.status))).all()
        for s, c in rows:
            if s in counts:
                counts[s] = c

        # Stock by brand
        brand_stmt = (
            select(VehicleBrand.name, func.count(InventoryUnit.id))
            .join(VehicleModel, VehicleModel.brand_id == VehicleBrand.id)
            .join(VehicleVariant, VehicleVariant.model_id == VehicleModel.id)
            .join(InventoryUnit, InventoryUnit.variant_id == VehicleVariant.id)
            .group_by(VehicleBrand.name)
        )
        brand_rows = (await self.session.execute(brand_stmt)).all()
        stock_by_brand = [{"brand": row[0], "count": row[1]} for row in brand_rows]

        return InventoryDashboardMetrics(
            total_units=total,
            available_units=counts["AVAILABLE"],
            reserved_units=counts["RESERVED"],
            sold_units=counts["SOLD"],
            service_units=counts["SERVICE"],
            in_transit_units=counts["IN_TRANSIT"],
            stock_by_brand=stock_by_brand,
        )

    async def get_revenue_metrics(self) -> RevenueDashboardMetrics:
        rev_stmt = select(func.coalesce(func.sum(Quotation.final_price), Decimal("0.00"))).where(Quotation.status == "ACCEPTED")
        total_rev = (await self.session.execute(rev_stmt)).scalar_one()

        pipe_stmt = select(func.coalesce(func.sum(Quotation.final_price), Decimal("0.00"))).where(Quotation.status.in_(["GENERATED", "SENT"]))
        pipe_rev = (await self.session.execute(pipe_stmt)).scalar_one()

        avg_stmt = select(func.coalesce(func.avg(Quotation.final_price), Decimal("0.00")))
        avg_rev = (await self.session.execute(avg_stmt)).scalar_one()

        by_var_stmt = (
            select(VehicleVariant.name, func.coalesce(func.sum(Quotation.final_price), Decimal("0.00")))
            .join(Quotation, Quotation.variant_id == VehicleVariant.id)
            .where(Quotation.status == "ACCEPTED")
            .group_by(VehicleVariant.name)
        )
        by_var_rows = (await self.session.execute(by_var_stmt)).all()
        rev_by_variant = [{"variant": row[0], "revenue": row[1]} for row in by_var_rows]

        return RevenueDashboardMetrics(
            total_revenue=total_rev,
            average_quotation_value=avg_rev.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            quotation_value_pipeline=pipe_rev,
            revenue_by_variant=rev_by_variant,
        )
