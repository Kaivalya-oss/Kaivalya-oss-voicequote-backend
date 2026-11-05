"""add details_json to quotation_items

Revision ID: 9e3f4a5b6c7d
Revises: 8d2e3f4a5b61
Create Date: 2026-11-05 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9e3f4a5b6c7d"
down_revision: Union[str, None] = "8d2e3f4a5b61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safe if column was already added directly
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'quotation_items' AND column_name = 'details_json'"
        )
    )
    if not result.first():
        op.add_column("quotation_items", sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("quotation_items", "details_json")

