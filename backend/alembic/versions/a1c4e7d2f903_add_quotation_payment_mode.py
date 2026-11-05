"""add quotation payment_mode

Revision ID: a1c4e7d2f903
Revises: 9e3f4a5b6c7d
Create Date: 2026-11-06 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c4e7d2f903"
down_revision: Union[str, None] = "9e3f4a5b6c7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column("payment_mode", sa.String(length=10), nullable=False, server_default="CASH"),
    )
    # Existing quotations priced with a loan were finance quotations.
    op.execute("UPDATE quotations SET payment_mode = 'FINANCE' WHERE loan_amount > 0 AND loan_tenure > 0")
    op.create_check_constraint("ck_quotations_payment_mode", "quotations", "payment_mode IN ('CASH', 'FINANCE')")


def downgrade() -> None:
    op.drop_constraint("ck_quotations_payment_mode", "quotations", type_="check")
    op.drop_column("quotations", "payment_mode")
