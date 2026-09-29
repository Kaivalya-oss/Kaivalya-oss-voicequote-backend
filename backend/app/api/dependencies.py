from typing import Callable, List, Optional
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_token
from app.database.dependencies import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository

security_bearer = HTTPBearer(auto_error=False)


async def get_client_ip(request: Request) -> str:
    """Extract client IP from headers or fallback to direct client host."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate Bearer token and retrieve the current active authenticated user."""
    if not auth or not auth.credentials:
        raise UnauthorizedException("Authentication token is missing.")

    payload = decode_token(auth.credentials)
    if payload.get("type") != "access":
        raise UnauthorizedException("Invalid token type. Access token required.")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedException("Token missing user subject.")

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(int(user_id))
    if not user:
        raise UnauthorizedException("User not found.")
    if not user.is_active:
        raise UnauthorizedException("User account is inactive.")

    return user


def require_roles(*allowed_roles: str) -> Callable[[User], User]:
    """Dependency factory that enforces Role-Based Access Control (RBAC)."""
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.name not in allowed_roles:
            raise ForbiddenException(
                f"Role '{current_user.role.name}' does not have permission to access this resource. Required: {list(allowed_roles)}"
            )
        return current_user

    return role_checker
