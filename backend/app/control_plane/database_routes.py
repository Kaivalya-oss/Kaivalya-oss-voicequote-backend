"""Owner-only management of the showroom's own database connection."""
import asyncio
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.models import ConnectionStatus, TenantConnection, TenantRole, TenantStatus
from app.control_plane.schemas import ConnectionDetails
from app.core.exceptions import AppException, ConflictException
from app.core.logging import logger
from app.core.tenant_context import TenantContext, require_tenant_roles
from app.core.config import settings
from app.database.connection_manager import build_engine, connection_manager, dispose_in_background
from app.database.control_plane import get_control_db
from app.database.provisioning import check_connection, provision_database, provisioning_in_progress, verify_schema
from app.database.secrets import SecretNotFound, get_secret_store
from app.database.tenant_connection import HOSTNAME_RE, TenantDatabaseError, TenantDbCredentials, classify_connection_error
from app.schemas.common import SuccessResponse

router = APIRouter(prefix="/platform/tenants/current/database", tags=["Showroom Database"])
owner_only = require_tenant_roles(TenantRole.OWNER)

DatabaseProvider = Literal["SUPABASE", "NEON", "AWS_RDS", "AZURE", "OTHER"]


class DatabaseConnectionRequest(BaseModel):
    """Separate fields, not a connection URL, so nothing can smuggle extra connection options."""

    provider: DatabaseProvider
    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(5432, ge=1024, le=65535)
    database: str = Field(..., min_length=1, max_length=63)
    username: str = Field(..., min_length=1, max_length=63)
    password: SecretStr = Field(..., min_length=1, max_length=512)
    ca_certificate: Optional[SecretStr] = Field(None, max_length=20000)

    @field_validator("host")
    @classmethod
    def _valid_host(cls, v: str) -> str:
        v = v.strip()
        if not HOSTNAME_RE.match(v):
            raise ValueError("Enter a host name or IP address only, without a scheme, port or path")
        return v

    def credentials(self) -> TenantDbCredentials:
        return TenantDbCredentials(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password.get_secret_value(),
            ca_certificate=self.ca_certificate.get_secret_value() if self.ca_certificate else None,
        )


class RotateCredentialsRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=1, max_length=63)
    password: SecretStr = Field(..., min_length=1, max_length=512)


class HealthResponse(BaseModel):
    healthy: bool
    error_code: Optional[str] = None
    checked_at: datetime


def _details(conn: TenantConnection) -> ConnectionDetails:
    return ConnectionDetails.model_validate(conn, from_attributes=True)


async def _locked_connection(session: AsyncSession, ctx: TenantContext) -> TenantConnection:
    return (
        await session.execute(
            select(TenantConnection)
            .where(TenantConnection.tenant_id == ctx.tenant.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _claim_provisioning(session: AsyncSession, ctx: TenantContext) -> tuple[TenantConnection, str]:
    """Atomically mark PROVISIONING so a double submit cannot run two setups."""
    conn = await _locked_connection(session, ctx)
    if provisioning_in_progress(conn):
        await session.rollback()
        raise ConflictException("DATABASE_SETUP_IN_PROGRESS", "Database setup is already running.")
    previous = conn.connection_status
    conn.connection_status = ConnectionStatus.PROVISIONING.value
    conn.last_error_code = None
    await session.commit()
    return conn, previous


async def _stored_credentials(conn: TenantConnection) -> TenantDbCredentials:
    if not conn.secret_reference:
        raise ConflictException("DATABASE_NOT_CONNECTED", "Connect a database first.")
    try:
        return TenantDbCredentials.from_secret(await asyncio.to_thread(get_secret_store().get, conn.secret_reference))
    except SecretNotFound:
        raise TenantDatabaseError("TENANT_DB_UNAVAILABLE")


@router.post("/test", response_model=SuccessResponse[dict])
async def test_database(req: DatabaseConnectionRequest, ctx: TenantContext = Depends(owner_only)):
    """Validate without storing anything."""
    return SuccessResponse(data=await check_connection(req.credentials()))


@router.post("/connect", response_model=SuccessResponse[ConnectionDetails])
async def connect_database(
    req: DatabaseConnectionRequest,
    ctx: TenantContext = Depends(owner_only),
    session: AsyncSession = Depends(get_control_db),
):
    """Connect or replace the database. The showroom activates only after everything is verified."""
    creds = req.credentials()
    conn, previous_status = await _claim_provisioning(session, ctx)
    old_reference = conn.secret_reference
    new_reference: Optional[str] = None
    try:
        version = await provision_database(session, ctx.tenant.id, creds)
        new_reference = await asyncio.to_thread(get_secret_store().put, ctx.tenant.id, creds.to_secret())
    except AppException as err:
        conn = await _locked_connection(session, ctx)
        # Replacing a working connection: a failed attempt must not take the showroom offline.
        conn.connection_status = (
            previous_status if previous_status == ConnectionStatus.ACTIVE.value else ConnectionStatus.FAILED.value
        )
        conn.last_error_code = err.code
        await session.commit()
        raise

    conn = await _locked_connection(session, ctx)
    conn.database_provider = req.provider
    conn.secret_reference = new_reference
    conn.connection_status = ConnectionStatus.ACTIVE.value
    conn.schema_version = version
    conn.last_health_check = datetime.now(timezone.utc)
    conn.last_error_code = None
    tenant = await session.merge(ctx.tenant)
    if tenant.status == TenantStatus.PENDING_SETUP.value:
        tenant.status = TenantStatus.ACTIVE.value
    await session.commit()

    await connection_manager.disconnect(ctx.tenant.id)
    if old_reference:
        await asyncio.to_thread(get_secret_store().delete, old_reference)
    return SuccessResponse(data=_details(conn))


@router.post("/migrate", response_model=SuccessResponse[ConnectionDetails])
async def migrate_database(ctx: TenantContext = Depends(owner_only), session: AsyncSession = Depends(get_control_db)):
    """Apply pending migrations, or retry after a failure. Safe to repeat."""
    conn, previous_status = await _claim_provisioning(session, ctx)
    try:
        creds = await _stored_credentials(conn)
        version = await provision_database(session, ctx.tenant.id, creds)
    except AppException as err:
        conn = await _locked_connection(session, ctx)
        conn.connection_status = previous_status
        conn.last_error_code = err.code
        await session.commit()
        raise
    conn = await _locked_connection(session, ctx)
    conn.connection_status = ConnectionStatus.ACTIVE.value
    conn.schema_version = version
    conn.last_error_code = None
    conn.last_health_check = datetime.now(timezone.utc)
    await session.commit()
    return SuccessResponse(data=_details(conn))


async def _probe_health(creds: TenantDbCredentials) -> Optional[str]:
    """Returns an error code. Every failure, including an unresolvable host, is a health result to record."""
    engine = None
    try:
        engine = await asyncio.to_thread(build_engine, creds, 1)
        async with engine.connect() as db:
            tls = (await db.execute(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"))).scalar_one()
            if tls is not True:
                return "TENANT_DB_TLS_FAILED"
        await verify_schema(engine)
        return None
    except AppException as err:
        return err.code
    except Exception:
        return "TENANT_DB_UNAVAILABLE"
    finally:
        if engine is not None:
            dispose_in_background(engine)


@router.post("/health", response_model=SuccessResponse[HealthResponse])
async def check_health(ctx: TenantContext = Depends(owner_only), session: AsyncSession = Depends(get_control_db)):
    conn = await _locked_connection(session, ctx)
    await session.commit()  # no row lock is held while talking to the showroom's database
    creds = await _stored_credentials(conn)
    try:
        error_code = await asyncio.wait_for(_probe_health(creds), timeout=settings.TENANT_DB_HEALTH_TIMEOUT)
    except TimeoutError:
        error_code = "TENANT_DB_TIMEOUT"
    now = datetime.now(timezone.utc)
    conn = await _locked_connection(session, ctx)
    conn.last_health_check = now
    conn.last_error_code = error_code
    await session.commit()
    return SuccessResponse(data=HealthResponse(healthy=error_code is None, error_code=error_code, checked_at=now))


@router.post("/rotate-credentials", response_model=SuccessResponse[ConnectionDetails])
async def rotate_credentials(
    req: RotateCredentialsRequest,
    ctx: TenantContext = Depends(owner_only),
    session: AsyncSession = Depends(get_control_db),
):
    """Verify the new credentials, store them as a new secret, then retire the old one."""
    conn = await _locked_connection(session, ctx)
    await session.commit()
    current = await _stored_credentials(conn)
    secret = current.to_secret() | {"password": req.password.get_secret_value()}
    if req.username:
        secret["username"] = req.username
    creds = TenantDbCredentials.from_secret(secret)

    engine = await asyncio.to_thread(build_engine, creds, 1)
    try:
        async with engine.connect() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise classify_connection_error(exc) from None
    finally:
        await engine.dispose()

    new_reference = await asyncio.to_thread(get_secret_store().put, ctx.tenant.id, creds.to_secret())
    conn = await _locked_connection(session, ctx)
    old_reference = conn.secret_reference
    conn.secret_reference = new_reference
    conn.last_error_code = None
    await session.commit()
    await connection_manager.disconnect(ctx.tenant.id)
    await asyncio.to_thread(get_secret_store().delete, old_reference)
    return SuccessResponse(data=_details(conn))


@router.post("/revoke", response_model=SuccessResponse[ConnectionDetails])
async def revoke_connection(ctx: TenantContext = Depends(owner_only), session: AsyncSession = Depends(get_control_db)):
    """Disconnect VoiceQuote from this database. The showroom's data stays untouched in its own database."""
    conn = await _locked_connection(session, ctx)
    reference = conn.secret_reference
    conn.secret_reference = None
    conn.connection_status = ConnectionStatus.REVOKED.value
    conn.last_error_code = None
    tenant = await session.merge(ctx.tenant)
    if tenant.status == TenantStatus.ACTIVE.value:
        tenant.status = TenantStatus.PENDING_SETUP.value
    await session.commit()
    await connection_manager.disconnect(ctx.tenant.id)
    if reference:
        await asyncio.to_thread(get_secret_store().delete, reference)
    logger.info(f"Tenant {ctx.tenant.id} revoked its database connection")
    return SuccessResponse(data=_details(conn))
