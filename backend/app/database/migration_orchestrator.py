"""Migrating showroom databases, one at a time, with explicit status and safe retries.

This runs in the main process's environment, because it needs each showroom's credentials. The Platform
Admin process can't reach it. While a showroom migrates, its staff get a clear 503 rather than errors from
half-changed tables. A failure rolls back that showroom's database and leaves the others unaffected.
"""
import asyncio
import uuid
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.models import ConnectionStatus, Tenant, TenantConnection, TenantStatus
from app.core.exceptions import AppException
from app.core.logging import logger
from app.database.control_plane import control_sessionmaker
from app.database.provisioning import latest_schema_version, provision_database, provisioning_in_progress
from app.database.secrets import SecretNotFound, get_secret_store
from app.database.tenant_connection import TenantDbCredentials
from app.platform_admin.models import AuditEvent

MIGRATABLE_STATUSES = (ConnectionStatus.ACTIVE.value, ConnectionStatus.FAILED.value, ConnectionStatus.PROVISIONING.value)


@dataclass(frozen=True)
class PlannedMigration:
    tenant_id: uuid.UUID
    public_code: str
    tenant_status: str
    connection_status: str
    schema_version: Optional[str]
    required_version: str

    @property
    def required(self) -> bool:
        return self.schema_version != self.required_version


@dataclass(frozen=True)
class MigrationResult:
    tenant_id: uuid.UUID
    public_code: str
    outcome: str  # SUCCEEDED, FAILED or SKIPPED
    schema_version: Optional[str] = None
    error_code: Optional[str] = None


def _audit(session: AsyncSession, action: str, tenant_id: uuid.UUID, before: dict, after: dict) -> None:
    session.add(AuditEvent(actor_type="SYSTEM", action=action, target_type="SHOWROOM", target_id=str(tenant_id), tenant_id=tenant_id, before=before, after=after))


async def plan_migrations() -> List[PlannedMigration]:
    """Every showroom with stored credentials. Reads central metadata only and changes nothing."""
    required = latest_schema_version()
    async with control_sessionmaker()() as session:
        rows = await session.execute(
            select(Tenant.id, Tenant.public_code, Tenant.status, TenantConnection.connection_status, TenantConnection.schema_version)
            .join(TenantConnection, TenantConnection.tenant_id == Tenant.id)
            .where(
                TenantConnection.secret_reference.is_not(None),
                TenantConnection.connection_status.in_(MIGRATABLE_STATUSES),
                Tenant.status != TenantStatus.CANCELLED.value,
            )
            .order_by(Tenant.created_at)
        )
        return [PlannedMigration(r[0], r[1], r[2], r[3], r[4], required) for r in rows]


async def migrate_showroom(tenant_id: uuid.UUID, public_code: str = "") -> MigrationResult:
    """One showroom. Safe to repeat, and safe to retry after a failure."""
    async with control_sessionmaker()() as session:
        conn = (await session.execute(select(TenantConnection).where(TenantConnection.tenant_id == tenant_id).with_for_update())).scalar_one_or_none()
        if conn is None or not conn.secret_reference:
            return MigrationResult(tenant_id, public_code, "SKIPPED", error_code="DATABASE_NOT_CONNECTED")
        if provisioning_in_progress(conn):
            await session.rollback()
            return MigrationResult(tenant_id, public_code, "SKIPPED", error_code="DATABASE_SETUP_IN_PROGRESS")
        previous_status = conn.connection_status if conn.connection_status != ConnectionStatus.PROVISIONING.value else ConnectionStatus.ACTIVE.value
        previous_version, reference = conn.schema_version, conn.secret_reference
        # Staff get TENANT_DB_MAINTENANCE for as long as this holds.
        conn.connection_status = ConnectionStatus.PROVISIONING.value
        conn.last_error_code = None
        await session.commit()

        version, error_code = None, None
        try:
            secret = await asyncio.to_thread(get_secret_store().get, reference)
            version = await provision_database(session, tenant_id, TenantDbCredentials.from_secret(secret))
        except SecretNotFound:
            error_code = "TENANT_DB_UNAVAILABLE"
        except AppException as err:
            error_code = err.code

        conn = (await session.execute(select(TenantConnection).where(TenantConnection.tenant_id == tenant_id).with_for_update().execution_options(populate_existing=True))).scalar_one()
        if error_code is None:
            conn.connection_status = ConnectionStatus.ACTIVE.value
            conn.schema_version = version
            conn.last_error_code = None
            _audit(session, "SHOWROOM_MIGRATION_SUCCEEDED", tenant_id, {"schema_version": previous_version}, {"schema_version": version})
        else:
            # The showroom's own transaction rolled back. Its stored version stays, so it's still reported as needing migration.
            conn.connection_status = previous_status
            conn.last_error_code = error_code
            _audit(session, "SHOWROOM_MIGRATION_FAILED", tenant_id, {"schema_version": previous_version}, {"error_code": error_code})
            logger.error(f"Tenant {tenant_id} migration failed: {error_code}")
        await session.commit()
    if error_code is None:
        return MigrationResult(tenant_id, public_code, "SUCCEEDED", schema_version=version)
    return MigrationResult(tenant_id, public_code, "FAILED", error_code=error_code)


async def migrate_all_required() -> List[MigrationResult]:
    """One at a time. A failure is recorded and the next showroom still runs."""
    results = []
    for item in await plan_migrations():
        if item.required:
            results.append(await migrate_showroom(item.tenant_id, item.public_code))
    return results
