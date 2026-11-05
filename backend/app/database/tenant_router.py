"""API request -> authenticated user -> membership -> connection reference -> tenant session."""
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import (
    TenantContext,
    decode_platform_token,
    resolve_tenant_context,
    tenant_not_operational,
)
from app.database.connection_manager import connection_manager
from app.database.control_plane import control_sessionmaker
from app.database.tenant_connection import TenantDatabaseError


async def resolve_operational_context(auth: Optional[HTTPAuthorizationCredentials]) -> TenantContext:
    """Uses a short control-plane session, released before any business work starts."""
    payload = decode_platform_token(auth)
    async with control_sessionmaker()() as control:
        ctx = await resolve_tenant_context(control, payload)
    if not ctx.operational:
        raise tenant_not_operational(ctx)
    return ctx


@asynccontextmanager
async def tenant_session(ctx: TenantContext) -> AsyncIterator[AsyncSession]:
    if ctx.connection is None or not ctx.connection.secret_reference:
        raise TenantDatabaseError("TENANT_DB_UNAVAILABLE")
    async with connection_manager.session(ctx.tenant.id, ctx.connection.secret_reference) as session:
        yield session


async def get_tenant_db(request: Request, auth: Optional[HTTPAuthorizationCredentials]) -> AsyncIterator[AsyncSession]:
    ctx = await resolve_operational_context(auth)
    request.state.tenant_context = ctx
    async with tenant_session(ctx) as session:
        yield session
