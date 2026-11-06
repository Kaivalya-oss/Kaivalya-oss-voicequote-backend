"""platform admin identity, sessions, audit trail and least-privilege role

Creates the NOLOGIN role voicequote_platform_admin, whose privileges exclude secret_reference,
showroom password hashes and invitation token hashes. The login role is created by the operator,
with a password that never appears in code. See docs/PLATFORM_ADMIN.md.

Revision ID: c1a1_platform_admin
Revises: c0a1_initial
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1a1_platform_admin"
down_revision: Union[str, None] = "c0a1_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE = "voicequote_platform_admin"


def upgrade() -> None:
    op.create_table(
        "platform_admins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("mfa_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("singleton", name="uq_platform_admins_singleton"),
        sa.UniqueConstraint("email", name="uq_platform_admins_email"),
        sa.CheckConstraint("singleton", name="ck_platform_admins_singleton"),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_platform_admins_status"),
    )
    op.create_table(
        "platform_admin_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("admin_id", sa.Uuid(), sa.ForeignKey("platform_admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mfa_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(40), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
    )
    op.create_index("ix_platform_admin_sessions_admin_id", "platform_admin_sessions", ["admin_id"])
    op.create_table(
        "platform_admin_recovery_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("admin_id", sa.Uuid(), sa.ForeignKey("platform_admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_platform_admin_recovery_codes_admin_id", "platform_admin_recovery_codes", ["admin_id"])
    op.create_table(
        "platform_admin_throttle",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])

    # Applies to every role, including the table owner, so ordinary code can't rewrite history.
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()"
    )

    # Roles belong to the server, not one database, so this must tolerate an existing role.
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON control_plane_alembic_version TO {ROLE}")

    # Its own tables. No INSERT or DELETE on platform_admins, so it can't create a second admin.
    op.execute(f"GRANT SELECT ON platform_admins TO {ROLE}")
    op.execute(
        f"GRANT UPDATE (mfa_secret_encrypted, mfa_enrolled_at, mfa_last_used_step, last_login_at, updated_at) "
        f"ON platform_admins TO {ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT ON platform_admin_sessions TO {ROLE}")
    op.execute(f"GRANT UPDATE (refresh_token_hash, last_used_at, revoked_at, revoked_reason) ON platform_admin_sessions TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON platform_admin_recovery_codes TO {ROLE}")
    op.execute(f"GRANT UPDATE (used_at) ON platform_admin_recovery_codes TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON platform_admin_throttle TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_events TO {ROLE}")

    # Showroom metadata, read-only and column by column. Excluded on purpose: secret_reference,
    # users.password_hash, users.auth_provider_user_id and invitation_token_hash.
    op.execute(f"GRANT SELECT ON tenants, subscriptions, subscription_events, tenant_migration_runs TO {ROLE}")
    op.execute(f"GRANT SELECT (id, email, full_name, account_status, created_at, updated_at) ON users TO {ROLE}")
    op.execute(
        f"GRANT SELECT (id, tenant_id, user_id, role, status, invited_by, invitation_expires_at, created_at, updated_at) "
        f"ON tenant_memberships TO {ROLE}"
    )
    op.execute(
        f"GRANT SELECT (id, tenant_id, database_provider, database_type, connection_status, schema_version, "
        f"last_health_check, last_error_code, created_at, updated_at) ON tenant_connections TO {ROLE}"
    )


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON tenants, subscriptions, subscription_events, tenant_migration_runs, users, tenant_memberships, tenant_connections, control_plane_alembic_version FROM {ROLE}")
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {ROLE}")
    op.execute("DROP TABLE audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")
    op.drop_table("platform_admin_throttle")
    op.drop_table("platform_admin_recovery_codes")
    op.drop_table("platform_admin_sessions")
    op.drop_table("platform_admins")
    # The role is left in place: it belongs to the server, and other databases may use it.
