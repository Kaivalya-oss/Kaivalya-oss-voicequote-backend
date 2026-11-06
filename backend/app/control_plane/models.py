"""Central platform tables, owned and hosted by VoiceQuote.

These tables hold accounts, memberships, subscriptions and connection metadata only. Customer,
vehicle, quotation, inventory and sales records belong in the showroom's own database and must
never be added here. This metadata is deliberately separate from app.models so business tables
cannot be created in, or queried through, the control-plane database by accident.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class ControlPlaneBase(DeclarativeBase):
    pass


class TenantStatus(str, Enum):
    PENDING_SETUP = "PENDING_SETUP"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"


class OnboardingStage(str, Enum):
    CREATED = "CREATED"  # central record exists, invitation not yet delivered
    INVITED = "INVITED"
    DATABASE_SETUP = "DATABASE_SETUP"  # Owner accepted, database not yet verified
    COMPLETE = "COMPLETE"


class SubscriptionStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELLED = "CANCELLED"


class AccountStatus(str, Enum):
    INVITED = "INVITED"  # no password yet; completed by accepting an invitation
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class TenantRole(str, Enum):
    OWNER = "OWNER"
    HEAD_OF_SALES = "HEAD_OF_SALES"
    SALESPERSON = "SALESPERSON"


class MembershipStatus(str, Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    DEACTIVATED = "DEACTIVATED"


class ConnectionStatus(str, Enum):
    PENDING = "PENDING"
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    REVOKED = "REVOKED"


def _values(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{m.value}'" for m in enum_cls)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Tenant(ControlPlaneBase, TimestampMixin):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(f"status IN ({_values(TenantStatus)})", name="ck_tenants_status"),
        CheckConstraint(f"subscription_status IN ({_values(SubscriptionStatus)})", name="ck_tenants_subscription_status"),
        CheckConstraint(f"onboarding_stage IN ({_values(OnboardingStage)})", name="ck_tenants_onboarding_stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    showroom_name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TenantStatus.PENDING_SETUP.value, nullable=False)
    subscription_status: Mapped[str] = mapped_column(
        String(20), default=SubscriptionStatus.PENDING.value, nullable=False
    )
    public_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    onboarding_stage: Mapped[str] = mapped_column(String(20), default=OnboardingStage.CREATED.value, nullable=False)
    # Registered business details, used later to verify an Owner recovery request.
    legal_business_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    business_registration_number: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    business_address: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    primary_owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_by_admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    suspension_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    connection: Mapped[Optional["TenantConnection"]] = relationship(back_populates="tenant", uselist=False)


class PlatformUser(ControlPlaneBase, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"account_status IN ({_values(AccountStatus)})", name="ck_users_account_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Reserved for an external identity provider; VoiceQuote's own auth leaves it empty.
    auth_provider_user_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    # bcrypt hash. Empty until an invited user sets a password.
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    account_status: Mapped[str] = mapped_column(String(20), default=AccountStatus.ACTIVE.value, nullable=False)


class TenantMembership(ControlPlaneBase, TimestampMixin):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"),
        CheckConstraint(f"role IN ({_values(TenantRole)})", name="ck_tenant_memberships_role"),
        CheckConstraint(f"status IN ({_values(MembershipStatus)})", name="ck_tenant_memberships_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # SHA-256 of the one-time invitation token; the token itself is never stored.
    invitation_token_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    invitation_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(lazy="joined")
    user: Mapped[PlatformUser] = relationship(foreign_keys=[user_id], lazy="joined")


class TenantConnection(ControlPlaneBase, TimestampMixin):
    """A reference to the showroom's database. Credentials live only in the secrets store."""

    __tablename__ = "tenant_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_connections_tenant"),
        CheckConstraint(f"connection_status IN ({_values(ConnectionStatus)})", name="ck_tenant_connections_status"),
        CheckConstraint("database_type = 'POSTGRESQL'", name="ck_tenant_connections_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    database_provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    database_type: Mapped[str] = mapped_column(String(20), default="POSTGRESQL", nullable=False)
    secret_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    connection_status: Mapped[str] = mapped_column(String(20), default=ConnectionStatus.PENDING.value, nullable=False)
    schema_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_health_check: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # A stable error code such as TENANT_DB_TLS_REQUIRED, never driver text that could echo credentials.
    last_error_code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="connection")


class Subscription(ControlPlaneBase, TimestampMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),
        CheckConstraint(f"status IN ({_values(SubscriptionStatus)})", name="ck_subscriptions_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(60), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(20), default="MONTHLY", nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=SubscriptionStatus.PENDING.value, nullable=False)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    next_billing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class SubscriptionEvent(ControlPlaneBase):
    __tablename__ = "subscription_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    # Must never contain showroom customer data.
    event_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class TenantMigrationRun(ControlPlaneBase):
    """Execution log of schema migrations on showroom databases (operational metadata only)."""

    __tablename__ = "tenant_migration_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    from_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    to_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # RUNNING, SUCCEEDED, FAILED
    error_code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # exception class name only
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
