from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import AsyncSessionLocal

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Basic service health check")
async def health_check():
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
    }


async def _health_session(request: Request):
    # Multi-tenant mode has no business database of its own: check the control plane, unauthenticated.
    if settings.MULTI_TENANT_ENABLED:
        from app.database.control_plane import control_sessionmaker

        async with control_sessionmaker()() as session:
            yield session
    else:
        async with AsyncSessionLocal() as session:
            yield session


@router.get("/health/db", summary="PostgreSQL database health check")
async def db_health_check(db: AsyncSession = Depends(_health_session)):
    try:
        result = await db.execute(text("SELECT 1;"))
        scalar = result.scalar()
        if scalar == 1:
            return {
                "status": "healthy",
                "database": "connected",
                "engine": "PostgreSQL (asyncpg)",
            }
        return {"status": "unhealthy", "database": "unexpected response"}
    except Exception as exc:
        return {
            "status": "unhealthy",
            "database": "error",
            "details": type(exc).__name__,
        }
