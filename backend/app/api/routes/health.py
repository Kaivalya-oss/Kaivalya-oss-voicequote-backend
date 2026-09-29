from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.dependencies import get_db

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Basic service health check")
async def health_check():
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
    }


@router.get("/health/db", summary="PostgreSQL database health check")
async def db_health_check(db: AsyncSession = Depends(get_db)):
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
            "details": str(exc),
        }
