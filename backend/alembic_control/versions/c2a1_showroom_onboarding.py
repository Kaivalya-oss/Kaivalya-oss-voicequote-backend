"""invitation-only onboarding and suspension records

Adds showroom business details, onboarding stage and suspension details to tenants. Gives the admin role
only the writes onboarding and suspension need. It still can't read or write secret_reference or
password_hash, and can write an invitation token hash but never read one.

Revision ID: c2a1_showroom_onboarding
Revises: c1a1_platform_admin
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2a1_showroom_onboarding"
down_revision: Union[str, None] = "c1a1_platform_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE = "voicequote_platform_admin"
STAGES = "'CREATED', 'INVITED', 'DATABASE_SETUP', 'COMPLETE'"


def upgrade() -> None:
    op.add_column("tenants", sa.Column("public_code", sa.String(20), nullable=True))
    op.add_column("tenants", sa.Column("onboarding_stage", sa.String(20), nullable=True))
    op.add_column("tenants", sa.Column("legal_business_name", sa.String(200), nullable=True))
    op.add_column("tenants", sa.Column("business_registration_number", sa.String(60), nullable=True))
    op.add_column("tenants", sa.Column("business_address", sa.String(300), nullable=True))
    op.add_column("tenants", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("tenants", sa.Column("state", sa.String(100), nullable=True))
    op.add_column("tenants", sa.Column("contact_phone", sa.String(20), nullable=True))
    op.add_column("tenants", sa.Column("primary_owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("tenants", sa.Column("created_by_admin_id", sa.Uuid(), nullable=True))
    op.add_column("tenants", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("suspended_by_admin_id", sa.Uuid(), nullable=True))
    op.add_column("tenants", sa.Column("suspension_reason", sa.Text(), nullable=True))

    # Showrooms that already exist.
    op.execute("UPDATE tenants SET public_code = 'VQ-' || upper(substr(md5(id::text), 1, 8))")
    op.execute("UPDATE tenants SET onboarding_stage = CASE WHEN status = 'ACTIVE' THEN 'COMPLETE' ELSE 'DATABASE_SETUP' END")
    op.execute(
        "UPDATE tenants t SET primary_owner_user_id = (SELECT m.user_id FROM tenant_memberships m "
        "WHERE m.tenant_id = t.id AND m.role = 'OWNER' AND m.status = 'ACTIVE' ORDER BY m.created_at LIMIT 1)"
    )
    op.alter_column("tenants", "public_code", nullable=False)
    op.alter_column("tenants", "onboarding_stage", nullable=False)
    op.create_unique_constraint("uq_tenants_public_code", "tenants", ["public_code"])
    op.create_check_constraint("ck_tenants_onboarding_stage", "tenants", f"onboarding_stage IN ({STAGES})")

    op.execute(
        f"GRANT INSERT (id, showroom_name, slug, status, subscription_status, public_code, onboarding_stage, "
        f"legal_business_name, business_registration_number, business_address, city, state, contact_phone, "
        f"primary_owner_user_id, created_by_admin_id, created_at, updated_at) ON tenants TO {ROLE}"
    )
    op.execute(
        f"GRANT UPDATE (status, onboarding_stage, primary_owner_user_id, suspended_at, suspended_by_admin_id, "
        f"suspension_reason, updated_at) ON tenants TO {ROLE}"
    )
    op.execute(f"GRANT INSERT (id, email, full_name, account_status, created_at, updated_at) ON users TO {ROLE}")
    op.execute(
        f"GRANT INSERT (id, tenant_id, user_id, role, status, invitation_token_hash, invitation_expires_at, created_at, updated_at) "
        f"ON tenant_memberships TO {ROLE}"
    )
    op.execute(f"GRANT UPDATE (status, invitation_token_hash, invitation_expires_at, updated_at) ON tenant_memberships TO {ROLE}")
    op.execute(f"GRANT INSERT (id, tenant_id, database_type, connection_status, created_at, updated_at) ON tenant_connections TO {ROLE}")
    op.execute(f"GRANT INSERT ON subscriptions, subscription_events TO {ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE INSERT ON subscriptions, subscription_events FROM {ROLE}")
    op.execute(f"REVOKE INSERT (id, tenant_id, database_type, connection_status, created_at, updated_at) ON tenant_connections FROM {ROLE}")
    op.execute(f"REVOKE UPDATE (status, invitation_token_hash, invitation_expires_at, updated_at) ON tenant_memberships FROM {ROLE}")
    op.execute(f"REVOKE INSERT (id, tenant_id, user_id, role, status, invitation_token_hash, invitation_expires_at, created_at, updated_at) ON tenant_memberships FROM {ROLE}")
    op.execute(f"REVOKE INSERT (id, email, full_name, account_status, created_at, updated_at) ON users FROM {ROLE}")
    op.execute(f"REVOKE UPDATE (status, onboarding_stage, primary_owner_user_id, suspended_at, suspended_by_admin_id, suspension_reason, updated_at) ON tenants FROM {ROLE}")
    op.execute(
        f"REVOKE INSERT (id, showroom_name, slug, status, subscription_status, public_code, onboarding_stage, legal_business_name, "
        f"business_registration_number, business_address, city, state, contact_phone, primary_owner_user_id, created_by_admin_id, created_at, updated_at) ON tenants FROM {ROLE}"
    )
    op.drop_constraint("ck_tenants_onboarding_stage", "tenants")
    op.drop_constraint("uq_tenants_public_code", "tenants")
    for column in ("suspension_reason", "suspended_by_admin_id", "suspended_at", "created_by_admin_id", "primary_owner_user_id", "contact_phone",
                   "state", "city", "business_address", "business_registration_number", "legal_business_name", "onboarding_stage", "public_code"):
        op.drop_column("tenants", column)
