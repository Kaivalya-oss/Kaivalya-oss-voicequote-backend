"""Validating, initializing and migrating a showroom's database.

The schema comes from the versioned chain in alembic/. Each run holds an advisory lock and a single
transaction: a failure rolls back completely, so a retry starts from a clean state. Existing tables
that VoiceQuote did not create are refused rather than altered. Upgrades never drop anything.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.control_plane.models import ConnectionStatus, TenantMigrationRun
from app.core.logging import logger
from app.database.connection_manager import build_engine
from app.database.tenant_connection import TenantDatabaseError, TenantDbCredentials, classify_connection_error
from app.models import Base
from app.models.user import Role

MIN_SERVER_VERSION = 130000
TENANT_ROLES = ("OWNER", "HEAD_OF_SALES", "SALESPERSON")
# Arbitrary fixed key for pg_advisory_xact_lock, unique to VoiceQuote schema changes.
MIGRATION_LOCK_KEY = 7283451902
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "alembic"))
    return config


def latest_schema_version() -> str:
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def _known_revisions() -> set[str]:
    return {rev.revision for rev in ScriptDirectory.from_config(_alembic_config()).walk_revisions()}


async def check_compatibility(engine: AsyncEngine) -> Optional[str]:
    """Pre-migration checks. Returns the schema version already present, if any."""
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()
            if int(version) < MIN_SERVER_VERSION:
                raise TenantDatabaseError("TENANT_DB_VERSION_UNSUPPORTED")
            is_super = (
                await conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
            ).scalar_one()
            if is_super:
                raise TenantDatabaseError("TENANT_DB_ROLE_TOO_PRIVILEGED")
            can_create = (
                await conn.execute(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')"))
            ).scalar_one()
            tables = set(
                (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))).scalars()
            )
            current = None
            if "alembic_version" in tables:
                current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
            if current is None and tables - {"alembic_version"}:
                raise TenantDatabaseError("TENANT_DB_SCHEMA_CONFLICT")
            if current is not None and current not in _known_revisions():
                raise TenantDatabaseError("TENANT_DB_SCHEMA_NEWER")
            if not can_create and current != latest_schema_version():
                raise TenantDatabaseError("TENANT_DB_PERMISSIONS")
            return current
    except TenantDatabaseError:
        raise
    except Exception as exc:
        raise classify_connection_error(exc) from None


def _upgrade(connection: Connection) -> None:
    config = _alembic_config()
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def _seed_required_rows(connection) -> None:
    await connection.execute(
        text("INSERT INTO roles (name, description) VALUES (:name, :name) ON CONFLICT (name) DO NOTHING"),
        [{"name": role} for role in TENANT_ROLES],
    )


async def apply_migrations(engine: AsyncEngine) -> str:
    async with engine.begin() as conn:
        # Two workers provisioning one database wait here rather than collide.
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        await conn.run_sync(_upgrade)
        await _seed_required_rows(conn)
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()


async def verify_schema(engine: AsyncEngine) -> None:
    expected = latest_schema_version()
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            if version != expected:
                raise TenantDatabaseError("TENANT_DB_VERIFICATION_FAILED")
            for table in Base.metadata.sorted_tables:
                await conn.execute(select(table).limit(1))
            roles = set((await conn.execute(select(Role.name))).scalars())
            if not set(TENANT_ROLES) <= roles:
                raise TenantDatabaseError("TENANT_DB_VERIFICATION_FAILED")
    except TenantDatabaseError:
        raise
    except Exception:
        raise TenantDatabaseError("TENANT_DB_VERIFICATION_FAILED") from None


async def check_connection(creds: TenantDbCredentials) -> dict:
    """Checks only: nothing is stored and nothing is changed."""
    engine = await asyncio.to_thread(build_engine, creds, 1)
    try:
        current = await check_compatibility(engine)
        return {"reachable": True, "tls": True, "schema_version": current, "latest_schema_version": latest_schema_version()}
    finally:
        await engine.dispose()


async def provision_database(control: AsyncSession, tenant_id: uuid.UUID, creds: TenantDbCredentials) -> str:
    """Compatibility check, migrations and verification, logged in the control plane (no credentials)."""
    engine = await asyncio.to_thread(build_engine, creds, 1)
    run: Optional[TenantMigrationRun] = None
    try:
        current = await check_compatibility(engine)
        run = TenantMigrationRun(
            tenant_id=tenant_id, from_version=current, to_version=latest_schema_version(), status="RUNNING"
        )
        control.add(run)
        await control.commit()
        try:
            version = await apply_migrations(engine)
        except TenantDatabaseError:
            raise
        except Exception as exc:
            # The class name only: driver messages can quote SQL or connection details.
            run.error_detail = type(exc).__name__
            logger.error(f"Tenant {tenant_id} migration failed: {type(exc).__name__}")
            raise TenantDatabaseError("TENANT_DB_MIGRATION_FAILED") from None
        await verify_schema(engine)
        run.status = "SUCCEEDED"
        run.finished_at = datetime.now(timezone.utc)
        await control.commit()
        return version
    except TenantDatabaseError as err:
        if run is not None:
            run.status = "FAILED"
            run.error_code = err.code
            run.finished_at = datetime.now(timezone.utc)
            await control.commit()
        raise
    finally:
        await engine.dispose()


STALE_PROVISIONING_SECONDS = 600


def provisioning_in_progress(connection) -> bool:
    if connection.connection_status != ConnectionStatus.PROVISIONING.value:
        return False
    age = (datetime.now(timezone.utc) - connection.updated_at).total_seconds()
    # An old PROVISIONING row means a crashed worker, and must not lock the Owner out forever.
    return age < STALE_PROVISIONING_SECONDS
