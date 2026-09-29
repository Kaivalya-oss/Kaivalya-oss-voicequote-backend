from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.customers import router as customers_router
from app.api.routes.vehicles import router as vehicles_router
from app.api.routes.inventory import router as inventory_router
from app.api.routes.offers import router as offers_router
from app.api.routes.finance import router as finance_router
from app.api.routes.quotations import router as quotations_router
from app.api.routes.leads import router as leads_router
from app.api.routes.followups import router as followups_router
from app.api.routes.test_rides import router as test_rides_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.reports import router as reports_router
from app.api.routes.ai import router as ai_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(customers_router)
api_v1_router.include_router(vehicles_router)
api_v1_router.include_router(inventory_router)
api_v1_router.include_router(offers_router)
api_v1_router.include_router(finance_router)
api_v1_router.include_router(quotations_router)
api_v1_router.include_router(leads_router)
api_v1_router.include_router(followups_router)
api_v1_router.include_router(test_rides_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(reports_router)
api_v1_router.include_router(ai_router)
