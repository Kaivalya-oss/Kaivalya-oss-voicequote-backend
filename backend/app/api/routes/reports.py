from datetime import date, datetime
from typing import List, Literal, Optional
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_roles
from app.core.permissions import FINANCIAL_SUMMARY_ROLES, MANAGEMENT_ROLES, RoleEnum
from app.services.access_policy import is_scoped_reader
from app.services.daily_export_service import DailyExportService, today_ist
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import SuccessResponse
from app.schemas.report import InventoryReportItem, LeadReportItem, SalesReportItem
from app.services.report_service import ReportService
from app.services.quotation_service import QuotationService
from app.schemas.quotation import QuotationResponse

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get(
    "/daily-export",
    summary="Download one day's activity (customers, leads, quotations, follow-ups, test rides) as Excel or CSV",
    responses={200: {"content": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
        "text/csv": {},
    }}},
)
async def daily_export(
    format: Literal["xlsx", "csv"] = Query("xlsx", description="xlsx (Excel workbook) or csv"),
    day: Optional[date] = Query(None, description="Day to export (YYYY-MM-DD, Indian time). Defaults to today."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES, RoleEnum.SALES_EXECUTIVE.value, RoleEnum.SALESPERSON.value)),
):
    day = day or today_ist()
    # Salespeople export only their own records; management exports everyone's
    scoped = is_scoped_reader(current_user)
    scope = f"Own records of {current_user.full_name}" if scoped else "All staff"
    sections = await DailyExportService(db).build(day, scoped_user_id=current_user.id if scoped else None)
    name = f"daily-update-{day.isoformat()}.{format}"
    # Rows keep each quotation's price. Only the total across quotations is restricted.
    totals = current_user.role.name in FINANCIAL_SUMMARY_ROLES
    if format == "csv":
        content, media = DailyExportService.to_csv(day, sections, scope, totals), "text/csv; charset=utf-8"
    else:
        content = DailyExportService.to_xlsx(day, sections, scope, totals)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get(
    "/sales",
    response_model=SuccessResponse[List[SalesReportItem]],
    summary="Generate accepted sales report with date range filters",
)
async def get_sales_report(
    from_date: Optional[datetime] = Query(None, description="Start date filter"),
    to_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*FINANCIAL_SUMMARY_ROLES)),
):
    service = ReportService(db)
    items = await service.get_sales_report(from_date=from_date, to_date=to_date, limit=limit)
    return SuccessResponse(data=items)


@router.get(
    "/leads",
    response_model=SuccessResponse[List[LeadReportItem]],
    summary="Generate sales pipeline leads report with date range filters",
)
async def get_leads_report(
    from_date: Optional[datetime] = Query(None, description="Start date filter"),
    to_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    service = ReportService(db)
    items = await service.get_leads_report(from_date=from_date, to_date=to_date, limit=limit)
    return SuccessResponse(data=items)


@router.get(
    "/inventory",
    response_model=SuccessResponse[List[InventoryReportItem]],
    summary="Generate inventory stock report",
)
async def get_inventory_report(
    status: Optional[str] = Query(None, description="Filter by unit status"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    service = ReportService(db)
    items = await service.get_inventory_report(status=status, limit=limit)
    return SuccessResponse(data=items)


@router.get(
    "/quotations",
    response_model=SuccessResponse[List[QuotationResponse]],
    summary="Generate quotation pipeline report",
)
async def get_quotations_report(
    from_date: Optional[datetime] = Query(None, description="Start date filter"),
    to_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    quote_service = QuotationService(db)
    quotes, _ = await quote_service.list_quotations(
        from_date=from_date,
        to_date=to_date,
        page=1,
        page_size=limit,
    )
    return SuccessResponse(data=[QuotationResponse.model_validate(q) for q in quotes])
