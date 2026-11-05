"""Keeps a staff row in the showroom's database in step with the central account and membership.

Business tables reference users.id, so each showroom has its own staff row linked by
central_user_id. Authorization always comes from the central membership; the row only supplies
the local id, the name shown on documents, and the role for existing permission checks. It holds
no password: this placeholder can never match a bcrypt hash.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.tenant_context import TenantContext
from app.database.tenant_connection import TenantDatabaseError
from app.models.user import Role, User

NO_PASSWORD = "!central-account"


async def _load(db: AsyncSession, ctx: TenantContext) -> User | None:
    return (
        await db.execute(
            select(User).where(User.central_user_id == ctx.user.id).options(selectinload(User.role))
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


async def current_staff_user(db: AsyncSession, ctx: TenantContext) -> User:
    role = (await db.execute(select(Role).where(Role.name == ctx.role))).scalar_one()
    full_name = ctx.user.full_name or ctx.user.email
    user = await _load(db, ctx)
    if user is None:
        # A row from before this showroom's database was linked. Reaching here already required
        # an active membership in this showroom, granted by its Owner to this email.
        existing = (
            await db.execute(select(User).where(User.email == ctx.user.email, User.central_user_id.is_(None)))
        ).scalars().first()
        if existing is not None:
            existing.central_user_id = ctx.user.id
            existing.password_hash = NO_PASSWORD
            await db.commit()
            user = await _load(db, ctx)
    if user is None:
        db.add(
            User(
                central_user_id=ctx.user.id,
                email=ctx.user.email,
                full_name=full_name,
                password_hash=NO_PASSWORD,
                role_id=role.id,
                is_active=True,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()  # a concurrent first request created it
        user = await _load(db, ctx)
    elif user.role_id != role.id or user.full_name != full_name or user.email != ctx.user.email:
        user.role_id = role.id
        user.full_name = full_name
        user.email = ctx.user.email
        await db.commit()
        user = await _load(db, ctx)
    if user is None:
        raise TenantDatabaseError("TENANT_DB_UNAVAILABLE")
    return user
