"""initial control plane

Revision ID: c0a1_initial
Revises:
Create Date: 2026-11-06 13:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c0a1_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("showroom_name", sa.String(150), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("subscription_status", sa.String(20), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('PENDING_SETUP', 'ACTIVE', 'SUSPENDED', 'CANCELLED')", name="ck_tenants_status"
        ),
        sa.CheckConstraint(
            "subscription_status IN ('PENDING', 'ACTIVE', 'PAST_DUE', 'CANCELLED')",
            name="ck_tenants_subscription_status",
        ),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("auth_provider_user_id", sa.String(255), nullable=True, unique=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("account_status", sa.String(20), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("account_status IN ('INVITED', 'ACTIVE', 'DISABLED')", name="ck_users_account_status"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("invited_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invitation_token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("invitation_expires_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"),
        sa.CheckConstraint("role IN ('OWNER', 'HEAD_OF_SALES', 'SALESPERSON')", name="ck_tenant_memberships_role"),
        sa.CheckConstraint("status IN ('INVITED', 'ACTIVE', 'DEACTIVATED')", name="ck_tenant_memberships_status"),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])

    op.create_table(
        "tenant_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("database_provider", sa.String(40), nullable=True),
        sa.Column("database_type", sa.String(20), nullable=False),
        sa.Column("secret_reference", sa.String(255), nullable=True),
        sa.Column("connection_status", sa.String(20), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=True),
        sa.Column("last_health_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(60), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_connections_tenant"),
        sa.CheckConstraint(
            "connection_status IN ('PENDING', 'PROVISIONING', 'ACTIVE', 'FAILED', 'REVOKED')",
            name="ck_tenant_connections_status",
        ),
        sa.CheckConstraint("database_type = 'POSTGRESQL'", name="ck_tenant_connections_type"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.String(60), nullable=False),
        sa.Column("billing_cycle", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("next_billing_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),
        sa.CheckConstraint("status IN ('PENDING', 'ACTIVE', 'PAST_DUE', 'CANCELLED')", name="ck_subscriptions_status"),
    )

    op.create_table(
        "subscription_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subscription_id", sa.Uuid(), sa.ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subscription_events_subscription_id", "subscription_events", ["subscription_id"])

    op.create_table(
        "tenant_migration_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_version", sa.String(64), nullable=True),
        sa.Column("to_version", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tenant_migration_runs_tenant_id", "tenant_migration_runs", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("tenant_migration_runs")
    op.drop_table("subscription_events")
    op.drop_table("subscriptions")
    op.drop_table("tenant_connections")
    op.drop_table("tenant_memberships")
    op.drop_table("users")
    op.drop_table("tenants")
