"""Tenant resolution and authorization for every multi-tenant request.

The active showroom comes only from the signed access token, and is re-verified against the
database on every request. Tenant IDs in headers, query strings or bodies are never trusted,
so changing one in a request grants nothing. Revoking a membership or changing a role takes
effect immediately, even for tokens that have not expired.
"""
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.models import (
    AccountStatus,
    ConnectionStatus,
    MembershipStatus,
    PlatformUser,
    Tenant,
    TenantConnection,
    TenantMembership,
    TenantRole,
    TenantStatus,
)
from app.core.exceptions import AppException, ForbiddenException, UnauthorizedException
from app.core.security import decode_token
from app.database.control_plane import get_control_db

PLATFORM_SCOPE = "platform"
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TenantContext:
    user: PlatformUser
    tenant: Tenant
    membership: TenantMembership
    connection: Optional[TenantConnection]

    @property
    def role(self) -> str:
        return self.membership.role

    @property
    def operational(self) -> bool:
        return (
            self.tenant.status == TenantStatus.ACTIVE.value
            and self.connection is not None
            and self.connection.connection_status == ConnectionStatus.ACTIVE.value
        )


def decode_platform_token(auth: Optional[HTTPAuthorizationCredentials]) -> Dict[str, Any]:
    if not auth or not auth.credentials:
        raise UnauthorizedException("Authentication token is missing.")
    payload = decode_token(auth.credentials)
    if payload.get("type") != "access" or payload.get("scope") != PLATFORM_SCOPE:
        raise UnauthorizedException("Invalid token.")
    return payload


async def load_active_user(session: AsyncSession, payload: Dict[str, Any]) -> PlatformUser:
    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except ValueError:
        raise UnauthorizedException("Invalid token.")
    user = await session.get(PlatformUser, user_id)
    if not user or user.account_status != AccountStatus.ACTIVE.value:
        raise UnauthorizedException("Account is not active.")
    return user


async def resolve_tenant_context(session: AsyncSession, payload: Dict[str, Any]) -> TenantContext:
    """Authenticated user -> selected tenant -> active membership -> role."""
    user = await load_active_user(session, payload)
    tid = payload.get("tid")
    if not tid:
        raise AppException(
            code="NO_ACTIVE_SHOWROOM",
            message="Select a showroom first.",
            status_code=status.HTTP_409_CONFLICT,
        )
    try:
        tenant_id = uuid.UUID(str(tid))
    except ValueError:
        raise UnauthorizedException("Invalid token.")

    membership = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user.id,
                TenantMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if membership is None or membership.tenant.status == TenantStatus.CANCELLED.value:
        raise ForbiddenException("You do not have access to this showroom.")

    connection = (
        await session.execute(select(TenantConnection).where(TenantConnection.tenant_id == tenant_id))
    ).scalar_one_or_none()
    return TenantContext(user=user, tenant=membership.tenant, membership=membership, connection=connection)


def tenant_not_operational(ctx: TenantContext) -> AppException:
    if ctx.tenant.status == TenantStatus.SUSPENDED.value:
        return AppException("TENANT_SUSPENDED", "This showroom is suspended.", status.HTTP_403_FORBIDDEN)
    return AppException(
        "TENANT_NOT_OPERATIONAL",
        "This showroom cannot be used until its database connection is set up and verified.",
        status.HTTP_403_FORBIDDEN,
    )


async def get_token_payload(auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Dict[str, Any]:
    return decode_platform_token(auth)


async def get_platform_user(
    payload: Dict[str, Any] = Depends(get_token_payload),
    session: AsyncSession = Depends(get_control_db),
) -> PlatformUser:
    return await load_active_user(session, payload)


async def get_tenant_context(
    payload: Dict[str, Any] = Depends(get_token_payload),
    session: AsyncSession = Depends(get_control_db),
) -> TenantContext:
    return await resolve_tenant_context(session, payload)


def require_tenant_roles(*roles: TenantRole) -> Callable[..., Awaitable[TenantContext]]:
    """Reusable dependency: an active member of the selected showroom holding one of these roles."""
    allowed = {r.value for r in roles}

    async def checker(ctx: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        if ctx.role not in allowed:
            raise ForbiddenException("Your role does not allow this action.")
        return ctx

    return checker


async def require_operational_tenant(ctx: TenantContext = Depends(get_tenant_context)) -> TenantContext:
    if not ctx.operational:
        raise tenant_not_operational(ctx)
    return ctx
