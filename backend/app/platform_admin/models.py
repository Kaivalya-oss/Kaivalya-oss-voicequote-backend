"""Platform Admin identity, sessions and the central audit trail.

Exactly one Platform Admin can exist: the `singleton` column has one permitted value and a unique
constraint, so a second row is refused by the database itself. Nothing here references showroom
memberships, so an administrator can't become a showroom member through this data.
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.control_plane.models import ControlPlaneBase, TimestampMixin, _utcnow


class PlatformAdminStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class PlatformAdmin(ControlPlaneBase, TimestampMixin):
    __tablename__ = "platform_admins"
    __table_args__ = (
        CheckConstraint("singleton", name="ck_platform_admins_singleton"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_platform_admins_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    singleton: Mapped[bool] = mapped_column(Boolean, default=True, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=PlatformAdminStatus.ACTIVE.value, nullable=False)
    # Encrypted with ADMIN_MFA_ENCRYPTION_KEY. Present but not enrolled while setup is unfinished.
    mfa_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mfa_enrolled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # The newest accepted time step, so a code can't be used twice.
    mfa_last_used_step: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformAdminSession(ControlPlaneBase):
    __tablename__ = "platform_admin_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform_admins.id", ondelete="CASCADE"), index=True, nullable=False)
    # SHA-256 of the current refresh token. Replaced on every use.
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mfa_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class PlatformAdminRecoveryCode(ControlPlaneBase):
    __tablename__ = "platform_admin_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform_admins.id", ondelete="CASCADE"), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class PlatformAdminThrottle(ControlPlaneBase):
    """Failure counters in the database, so limits hold across processes and restarts."""

    __tablename__ = "platform_admin_throttle"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(ControlPlaneBase):
    """Append-only: database triggers refuse update, delete and truncate for every role.

    target ids aren't foreign keys, so history survives whatever happens to the target later.
    Never store passwords, tokens, MFA secrets, recovery codes or database credentials here.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # PLATFORM_ADMIN, SYSTEM or ANONYMOUS
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, index=True, nullable=True)
    before: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
