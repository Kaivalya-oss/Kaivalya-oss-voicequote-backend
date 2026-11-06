"""Authorization for every Platform Admin route. There's one guard, and showroom credentials can't pass it."""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.platform_admin import security
from app.platform_admin.config import get_admin_settings
from app.platform_admin.database import get_admin_db
from app.platform_admin.models import PlatformAdmin, PlatformAdminSession, PlatformAdminStatus
from app.platform_admin.service import AdminAuthService, RequestMeta

bearer = HTTPBearer(auto_error=False)
LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


def client_ip(request: Request) -> Optional[str]:
    """The connecting address. X-Forwarded-For counts only when that address is a configured proxy."""
    peer = request.client.host if request.client else None
    trusted = get_admin_settings().trusted_proxies
    if peer and peer in trusted:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        for hop in reversed(hops):
            if hop not in trusted:
                return hop[:64]
    return peer[:64] if peer else None


def request_meta(request: Request) -> RequestMeta:
    agent = request.headers.get("user-agent")
    return RequestMeta(ip_address=client_ip(request), user_agent=agent[:255] if agent else None)


def require_allowed_origin(request: Request) -> None:
    """For the cookie-authenticated route: a request must come from a configured admin origin."""
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin not in get_admin_settings().allowed_origins:
        raise AppException("ADMIN_ORIGIN_NOT_ALLOWED", "This request's origin isn't allowed.", status.HTTP_403_FORBIDDEN)


@dataclass(frozen=True)
class AdminContext:
    admin: PlatformAdmin
    session: PlatformAdminSession
    meta: RequestMeta


async def require_platform_admin(
    request: Request,
    auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_admin_db),
) -> AdminContext:
    """Token, then the session and account in the database on every request, so revocation is immediate."""
    if not auth or not auth.credentials:
        raise security.AdminAuthError()
    payload = security.decode_token(auth.credentials, security.TYPE_ACCESS)
    if payload.get("mfa") is not True:
        raise security.AdminAuthError()
    try:
        admin_id, session_id = uuid.UUID(payload["sub"]), uuid.UUID(str(payload.get("sid")))
    except ValueError:
        raise security.AdminAuthError() from None

    meta = request_meta(request)
    service = AdminAuthService(db, meta)
    session = await db.get(PlatformAdminSession, session_id, populate_existing=True)
    admin = await db.get(PlatformAdmin, admin_id, populate_existing=True)
    if (
        session is None
        or admin is None
        or session.admin_id != admin.id
        or service.session_problem(session)
        or admin.status != PlatformAdminStatus.ACTIVE.value
        or admin.mfa_enrolled_at is None
    ):
        raise security.AdminAuthError()

    now = datetime.now(timezone.utc)
    if now - session.last_used_at >= LAST_USED_WRITE_INTERVAL:
        session.last_used_at = now
        await db.commit()
    return AdminContext(admin, session, meta)
