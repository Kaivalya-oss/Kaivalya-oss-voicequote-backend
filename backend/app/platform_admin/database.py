"""The admin process's only database connection: the central database, as a least-privilege role.

There's no route from here to a showroom database. This package never imports the tenant connection
manager or secrets store, and a test fails the build if that changes. The startup check confirms
the connected role really lacks the privileges it must not have.
"""
from typing import AsyncGenerator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.platform_admin.config import get_admin_settings

_engine: Optional[AsyncEngine] = None
_sessions: Optional[async_sessionmaker[AsyncSession]] = None


def admin_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessions
    if _sessions is None:
        _engine = create_async_engine(
            get_admin_settings().ADMIN_DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5, hide_parameters=True
        )
        _sessions = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    return _sessions


async def get_admin_db() -> AsyncGenerator[AsyncSession, None]:
    async with admin_sessionmaker()() as session:
        yield session


async def dispose_admin_engine() -> None:
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessions = None, None


# Each query must return false. Anything else means the role can do something it must never do.
FORBIDDEN_PRIVILEGE_CHECKS = {
    "superuser": "SELECT rolsuper FROM pg_roles WHERE rolname = current_user",
    "bypass row security": "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user",
    "create roles": "SELECT rolcreaterole FROM pg_roles WHERE rolname = current_user",
    "create databases": "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user",
    "read tenant secret references": "SELECT has_column_privilege('tenant_connections', 'secret_reference', 'SELECT')",
    "read showroom password hashes": "SELECT has_column_privilege('users', 'password_hash', 'SELECT')",
    "read invitation token hashes": "SELECT has_column_privilege('tenant_memberships', 'invitation_token_hash', 'SELECT')",
    "write tenant secret references": "SELECT has_column_privilege('tenant_connections', 'secret_reference', 'UPDATE') OR has_column_privilege('tenant_connections', 'secret_reference', 'INSERT')",
    "set showroom passwords": "SELECT has_column_privilege('users', 'password_hash', 'UPDATE') OR has_column_privilege('users', 'password_hash', 'INSERT')",
    "change a showroom member's role": "SELECT has_column_privilege('tenant_memberships', 'role', 'UPDATE')",
    "create a platform admin": "SELECT has_table_privilege('platform_admins', 'INSERT')",
    "delete a platform admin": "SELECT has_table_privilege('platform_admins', 'DELETE')",
    "change the admin's email or password": "SELECT has_column_privilege('platform_admins', 'password_hash', 'UPDATE')",
    "change audit events": "SELECT has_table_privilege('audit_events', 'UPDATE')",
    "delete audit events": "SELECT has_table_privilege('audit_events', 'DELETE')",
    "delete payments or showrooms": "SELECT has_table_privilege('tenants', 'DELETE')",
    "create tables": "SELECT has_schema_privilege('public', 'CREATE')",
    "read or end showroom sign-in sessions": "SELECT has_table_privilege('showroom_sessions', 'SELECT') OR has_table_privilege('showroom_sessions', 'UPDATE')",
}


async def boundary_violations(session: AsyncSession) -> list[str]:
    found = []
    for name, query in FORBIDDEN_PRIVILEGE_CHECKS.items():
        if (await session.execute(text(query))).scalar_one():
            found.append(name)
    return found


async def schema_version(session: AsyncSession) -> Optional[str]:
    return (await session.execute(text("SELECT version_num FROM control_plane_alembic_version"))).scalar_one_or_none()
