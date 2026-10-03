"""Staff directory and account approval (management only).

Self-registered accounts start inactive; ADMIN or DEALER_OWNER activates them here.
Only an ADMIN may change a user's role.
"""
import math
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, require_roles
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.core.permissions import MANAGEMENT_ROLES, RoleEnum
from app.database.dependencies import get_db
from app.models.user import Role, User
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import UserResponse
from app.schemas.common import PaginatedResponse, SuccessResponse

router = APIRouter(prefix="/users", tags=["Users"])


class UserRoleUpdate(BaseModel):
    role_name: str = Field(..., pattern=r"^(ADMIN|DEALER_OWNER|SALES_EXECUTIVE|FINANCE_EXECUTIVE|INVENTORY_MANAGER)$")


async def _get_user(db: AsyncSession, user_id: int) -> User:
    user = await UserRepository(db).get_by_id(user_id)
    if not user:
        raise NotFoundException("User", user_id)
    return user


@router.get("", response_model=PaginatedResponse[UserResponse], summary="List staff accounts (management)")
async def list_users(
    is_active: Optional[bool] = Query(None, description="Filter by active flag (false = pending approval / deactivated)"),
    role: Optional[str] = Query(None, description="Filter by role name"),
    search: Optional[str] = Query(None, description="Search name or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
):
    filters = []
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    if role:
        filters.append(User.role.has(Role.name == role.upper().strip()))
    if search:
        term = f"%{search.strip()}%"
        filters.append(User.full_name.ilike(term) | User.email.ilike(term))
    total = (await db.execute(select(func.count(User.id)).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(User).where(*filters).order_by(User.is_active.asc(), User.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return PaginatedResponse(
        items=[UserResponse.model_validate(u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )


async def _set_active(db: AsyncSession, actor: User, user_id: int, active: bool, ip: str) -> User:
    user = await _get_user(db, user_id)
    if user.id == actor.id and not active:
        raise BadRequestException("CANNOT_DEACTIVATE_SELF", "You cannot deactivate your own account.")
    if user.role.name == RoleEnum.ADMIN.value and actor.role.name != RoleEnum.ADMIN.value:
        raise ForbiddenException("Only an administrator can change an administrator account.")
    user.is_active = active
    await db.commit()
    await AuditLogRepository(db).log_action(
        user_id=actor.id,
        action="USER_ACTIVATED" if active else "USER_DEACTIVATED",
        entity_type="USER",
        entity_id=user.id,
        new_value={"is_active": active},
        ip_address=ip,
    )
    return await _get_user(db, user_id)


@router.post("/{id}/activate", response_model=SuccessResponse[UserResponse], summary="Approve / re-activate an account")
async def activate_user(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    return SuccessResponse(data=UserResponse.model_validate(await _set_active(db, current_user, id, True, ip_address)))


@router.post("/{id}/deactivate", response_model=SuccessResponse[UserResponse], summary="Deactivate an account")
async def deactivate_user(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGEMENT_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    return SuccessResponse(data=UserResponse.model_validate(await _set_active(db, current_user, id, False, ip_address)))


@router.put("/{id}/role", response_model=SuccessResponse[UserResponse], summary="Change a user's role (ADMIN only)")
async def change_user_role(
    id: int,
    req: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(RoleEnum.ADMIN.value)),
    ip_address: str = Depends(get_client_ip),
):
    user = await _get_user(db, id)
    if user.id == current_user.id:
        raise BadRequestException("CANNOT_CHANGE_OWN_ROLE", "You cannot change your own role.")
    role = await UserRepository(db).get_role_by_name(req.role_name)
    if not role:
        raise BadRequestException("ROLE_NOT_CONFIGURED", f"Role '{req.role_name}' is not configured.")
    old = user.role.name
    user.role_id = role.id
    await db.commit()
    await AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="USER_ROLE_CHANGED",
        entity_type="USER",
        entity_id=user.id,
        old_value={"role": old},
        new_value={"role": role.name},
        ip_address=ip_address,
    )
    db.expire_all()
    return SuccessResponse(data=UserResponse.model_validate(await _get_user(db, id)))
