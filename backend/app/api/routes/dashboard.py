from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_roles
from app.core.permissions import MANAGEMENT_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import SuccessResponse
from app.schemas.dashboard import (
    DashboardSummary,
    InventoryDashboardMetrics,
    LeadsDashboardMetrics,
    RevenueDashboardMetrics,
    SalesDashboardMetrics,
)
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get(
    "/summary",
    response_model=SuccessResponse[DashboardSummary],
    summary="Get overall dealership KPI performance metrics",
)
async def get_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    service = DashboardService(db)
    summary = await service.get_summary()
    return SuccessResponse(data=summary)


@router.get(
    "/sales",
    response_model=SuccessResponse[SalesDashboardMetrics],
    summary="Get sales team conversion metrics",
)
async def get_sales_metrics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    service = DashboardService(db)
    metrics = await service.get_sales_metrics()
    return SuccessResponse(data=metrics)


@router.get(
    "/leads",
    response_model=SuccessResponse[LeadsDashboardMetrics],
    summary="Get pipeline leads breakdown by stage, source, priority",
)
async def get_leads_metrics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    service = DashboardService(db)
    metrics = await service.get_leads_metrics()
    return SuccessResponse(data=metrics)


@router.get(
    "/inventory",
    response_model=SuccessResponse[InventoryDashboardMetrics],
    summary="Get inventory stock distribution metrics",
)
async def get_inventory_metrics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    service = DashboardService(db)
    metrics = await service.get_inventory_metrics()
    return SuccessResponse(data=metrics)


@router.get(
    "/revenue",
    response_model=SuccessResponse[RevenueDashboardMetrics],
    summary="Get revenue, pipeline values, and model contributions",
)
async def get_revenue_metrics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    service = DashboardService(db)
    metrics = await service.get_revenue_metrics()
    return SuccessResponse(data=metrics)
