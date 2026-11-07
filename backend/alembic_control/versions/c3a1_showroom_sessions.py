"""showroom sign-in sessions

One row per sign-in, so a refresh token can be revoked on sign-out and a reused one detected.
Only the SHA-256 of the current refresh token's ID is stored. The Platform Admin's role gets no
access to this table.

Revision ID: c3a1_showroom_sessions
Revises: c2a1_showroom_onboarding
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a1_showroom_sessions"
down_revision: Union[str, None] = "c2a1_showroom_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "showroom_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(30), nullable=True),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('SIGNED_OUT', 'REFRESH_REUSED')",
            name="ck_showroom_sessions_revoked_reason",
        ),
    )
    op.create_index("ix_showroom_sessions_user_id", "showroom_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_showroom_sessions_user_id", table_name="showroom_sessions")
    op.drop_table("showroom_sessions")
