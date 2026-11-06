"""Daily activity export ("today's update") as an Excel workbook or a CSV file."""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.followup import Followup
from app.models.lead import Lead, LeadStatusHistory
from app.models.quotation import Quotation
from app.models.test_ride import TestRide
from app.models.user import User

# The dealership works in Indian time (no daylight saving), so "today" is an IST calendar day.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


@dataclass
class Section:
    title: str
    headers: List[str]
    rows: List[List[Any]] = field(default_factory=list)
    money_columns: tuple = ()


def ist_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=IST)
    return start, start + timedelta(days=1)


def today_ist() -> date:
    return datetime.now(IST).date()


def _t(value: Optional[datetime]) -> str:
    """Local time of day, or date + time when the value falls on another day."""
    if value is None:
        return ""
    return value.astimezone(IST).strftime("%d-%m-%Y %H:%M")


def _label(value: Optional[str]) -> str:
    return value.replace("_", " ").title() if value else ""


def _money(value: Optional[Decimal]) -> Optional[float | int]:
    if value is None:
        return None
    # whole rupees as ints so CSV shows 169500, not 169500.0
    return int(value) if value == int(value) else round(float(value), 2)


def _vehicle(variant) -> str:
    if variant is None:
        return ""
    model = getattr(variant, "model", None)
    brand = getattr(model, "brand", None) if model else None
    brand_name = getattr(brand, "name", "") or ""
    model_name = getattr(model, "name", "") or ""
    name = variant.name or ""
    # Variant names often already contain the model ("Aerox 155 MotoGP Edition") or brand ("Ather 450X")
    lowered = name.lower()
    parts = [
        brand_name if brand_name and brand_name.split()[0].lower() not in lowered else "",
        model_name if model_name and model_name.lower() not in lowered else "",
        name,
    ]
    return " ".join(p for p in parts if p)


class DailyExportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def build(self, day: date, scoped_user_id: Optional[int]) -> List[Section]:
        """All activity on `day`. `scoped_user_id` limits it to one salesperson's own records."""
        start, end = ist_day_bounds(day)
        in_day = lambda col: and_(col >= start, col < end)  # noqa: E731
        users = {u.id: u.full_name for u in (await self.session.execute(select(User))).scalars()}
        who = lambda uid: users.get(uid, "") if uid else ""  # noqa: E731

        # --- New customers ---
        q = select(Customer).where(in_day(Customer.created_at)).order_by(Customer.created_at)
        if scoped_user_id:
            q = q.where(Customer.created_by_user_id == scoped_user_id)
        customers = (await self.session.execute(q)).scalars().all()
        customers_section = Section(
            "New customers",
            ["Time", "Customer ID", "Name", "Phone", "Email", "City", "Address", "Added by"],
            [[_t(c.created_at), c.id, c.full_name, c.phone, c.email or "", c.city or "", c.address or "",
              who(c.created_by_user_id)] for c in customers],
        )

        # --- Lead activity: every stage change today, including newly opened leads ---
        q = (
            select(LeadStatusHistory, Lead)
            .join(Lead, Lead.id == LeadStatusHistory.lead_id)
            .where(in_day(LeadStatusHistory.created_at))
            .order_by(LeadStatusHistory.created_at)
        )
        if scoped_user_id:
            q = q.where(Lead.assigned_salesperson_id == scoped_user_id)
        history = (await self.session.execute(q)).unique().all()
        lead_section = Section(
            "Lead activity",
            ["Time", "Lead ID", "Customer", "Phone", "Vehicle", "From", "To", "Priority", "Source",
             "Changed by", "Reason"],
            [[_t(h.created_at), lead.id, lead.customer.full_name if lead.customer else "",
              lead.customer.phone if lead.customer else "", _vehicle(lead.interested_variant),
              _label(h.from_status) if (h.from_status or "NONE").upper() != "NONE" else "New lead", _label(h.to_status), _label(lead.priority),
              _label(lead.source), who(h.changed_by_user_id), h.reason or ""]
             for h, lead in history],
        )

        # --- Quotations created or updated today ---
        q = (
            select(Quotation)
            .where(or_(in_day(Quotation.created_at), in_day(Quotation.updated_at)))
            .order_by(Quotation.created_at)
        )
        if scoped_user_id:
            q = q.where(Quotation.sales_executive_id == scoped_user_id)
        quotes = (await self.session.execute(q)).unique().scalars().all()

        def option_b(x) -> list:
            """Vehicle, on-road price and discount of Option B; blank for a single-vehicle quotation."""
            options = [it for it in x.items if it.item_type == "VEHICLE_OPTION"]
            if len(options) < 2:
                return ["", None, None]
            snap = options[1].details_json or {}
            discount = snap.get("discount")
            return [
                snap.get("full_name") or options[1].item_name,
                _money(options[1].total_price),
                _money(Decimal(str(discount))) if discount is not None else None,
            ]

        quote_section = Section(
            "Quotations",
            ["Created", "Quotation No.", "Customer", "Phone", "Vehicle (Option A)", "On-road price A (₹)",
             "Discount A (₹)", "Option B vehicle", "On-road price B (₹)", "Discount B (₹)", "Payment mode",
             "Status", "Salesperson", "Last update"],
            [[_t(x.created_at), x.quotation_number, x.customer.full_name if x.customer else "",
              x.customer.phone if x.customer else "", _vehicle(x.variant), _money(x.final_price),
              _money(x.discount), *option_b(x), _label(x.payment_mode), _label(x.status),
              who(x.sales_executive_id), _t(x.updated_at)] for x in quotes],
            money_columns=(5, 6, 8, 9),
        )

        # --- Follow-ups due today or completed today ---
        q = (
            select(Followup)
            .where(or_(in_day(Followup.scheduled_at), in_day(Followup.completed_at)))
            .order_by(Followup.scheduled_at)
        )
        if scoped_user_id:
            q = q.where(Followup.assigned_user_id == scoped_user_id)
        followups = (await self.session.execute(q)).unique().scalars().all()
        followup_section = Section(
            "Follow-ups",
            ["Scheduled", "Type", "Status", "Customer", "Phone", "Lead ID", "Assigned to", "Completed",
             "Notes"],
            [[_t(f.scheduled_at), _label(f.type), _label(f.status),
              f.lead.customer.full_name if f.lead and f.lead.customer else "",
              f.lead.customer.phone if f.lead and f.lead.customer else "", f.lead_id,
              who(f.assigned_user_id), _t(f.completed_at), f.notes or ""] for f in followups],
        )

        # --- Test rides scheduled today or booked today ---
        q = (
            select(TestRide)
            .where(or_(in_day(TestRide.scheduled_at), in_day(TestRide.created_at)))
            .order_by(TestRide.scheduled_at)
        )
        if scoped_user_id:
            q = q.where(TestRide.sales_executive_id == scoped_user_id)
        rides = (await self.session.execute(q)).unique().scalars().all()
        ride_section = Section(
            "Test rides",
            ["Scheduled", "Duration (min)", "Status", "Customer", "Phone", "Vehicle", "Salesperson",
             "Rating (1-5)", "Notes"],
            [[_t(r.scheduled_at), r.duration_minutes, _label(r.status),
              r.customer.full_name if r.customer else "", r.customer.phone if r.customer else "",
              _vehicle(r.variant), who(r.sales_executive_id), r.feedback_rating or "", r.notes or ""]
             for r in rides],
        )

        return [customers_section, lead_section, quote_section, followup_section, ride_section]

    # ---------- Writers ----------

    @staticmethod
    def summary_rows(day: date, sections: List[Section], scope: str, financial_totals: bool = False) -> List[List[Any]]:
        quotes = next(s for s in sections if s.title == "Quotations")
        # Option B column set = a comparison of alternatives, which has no single amount to total.
        comparisons = sum(1 for r in quotes.rows if r[7])
        value = sum(r[5] or 0 for r in quotes.rows if not r[7])
        return [
            ["Date", day.strftime("%d-%m-%Y")],
            ["Generated", datetime.now(IST).strftime("%d-%m-%Y %H:%M IST")],
            ["Scope", scope],
            [],
            *[[s.title, len(s.rows)] for s in sections],
            *([["Single-vehicle quotation value (₹)", value]] if financial_totals else []),
            ["Comparison quotations (not totalled)", comparisons],
        ]

    @staticmethod
    def to_csv(day: date, sections: List[Section], scope: str, financial_totals: bool = False) -> bytes:
        """One CSV with each section stacked under its own heading."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([f"Daily update — {day.strftime('%d-%m-%Y')}"])
        for row in DailyExportService.summary_rows(day, sections, scope, financial_totals):
            writer.writerow(row)
        for s in sections:
            writer.writerow([])
            writer.writerow([s.title.upper()])
            writer.writerow(s.headers)
            if not s.rows:
                writer.writerow(["(none)"])
            writer.writerows(["" if v is None else v for v in row] for row in s.rows)
        # BOM so Excel opens ₹ and non-English names correctly
        return ("﻿" + buf.getvalue()).encode("utf-8")

    @staticmethod
    def to_xlsx(day: date, sections: List[Section], scope: str, financial_totals: bool = False) -> bytes:
        wb = Workbook()
        bold = Font(bold=True)
        head_fill = PatternFill("solid", fgColor="DCE6F1")

        summary = wb.active
        summary.title = "Summary"
        summary.append([f"Daily update — {day.strftime('%d-%m-%Y')}"])
        summary["A1"].font = Font(bold=True, size=14)
        for row in DailyExportService.summary_rows(day, sections, scope, financial_totals):
            summary.append(row)
        for row in summary.iter_rows(min_row=2):
            row[0].font = bold
        summary.cell(row=summary.max_row - 1, column=2).number_format = "#,##0"
        summary.column_dimensions["A"].width = 36
        summary.column_dimensions["B"].width = 24

        for s in sections:
            ws = wb.create_sheet(s.title[:31])
            ws.append(s.headers)
            for cell in ws[1]:
                cell.font = bold
                cell.fill = head_fill
                cell.alignment = Alignment(vertical="center")
            for row in s.rows:
                ws.append(row)
            ws.freeze_panes = "A2"
            for col in s.money_columns:
                for (cell,) in ws.iter_rows(min_row=2, min_col=col + 1, max_col=col + 1):
                    cell.number_format = "#,##0"
            for idx, header in enumerate(s.headers, start=1):
                longest = max([len(str(header))] + [len(str(r[idx - 1] or "")) for r in s.rows])
                ws.column_dimensions[get_column_letter(idx)].width = min(max(longest + 2, 10), 50)
            if not s.rows:
                ws.append(["No activity on this day."])

        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
