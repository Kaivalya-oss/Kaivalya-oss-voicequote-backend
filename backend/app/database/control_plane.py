"""Sessions for the central VoiceQuote database. Business queries never use these."""
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def control_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        if not settings.CONTROL_PLANE_DATABASE_URL:
            raise RuntimeError("CONTROL_PLANE_DATABASE_URL is required when MULTI_TENANT_ENABLED is true.")
        _engine = create_async_engine(
            settings.CONTROL_PLANE_DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10
        )
        _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    return _sessionmaker


def control_engine() -> AsyncEngine:
    control_sessionmaker()
    return _engine


async def get_control_db() -> AsyncGenerator[AsyncSession, None]:
    async with control_sessionmaker()() as session:
        yield session


async def dispose_control_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
