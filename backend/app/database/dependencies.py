from typing import AsyncGenerator, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import AsyncSessionLocal

_bearer = HTTPBearer(auto_error=False)


async def get_db(
    request: Request,
    auth: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AsyncGenerator[AsyncSession, None]:
    """Business-data session: the showroom's own database in multi-tenant mode."""
    if settings.MULTI_TENANT_ENABLED:
        from app.database.tenant_router import get_tenant_db

        async for session in get_tenant_db(request, auth):
            yield session
        return

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
