from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_roles
from app.core.permissions import MANAGEMENT_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import SuccessResponse
from app.schemas.report import InventoryReportItem, LeadReportItem, SalesReportItem
from app.services.report_service import ReportService
from app.services.quotation_service import QuotationService
from app.schemas.quotation import QuotationResponse

router = APIRouter(prefix="/reports", tags=["Reports"])


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
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
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
