"""hardening: quotation idempotency, reservation owner, status/name constraints

Revision ID: 7b1c2d3e4f50
Revises: 4e486bc5d2f2
Create Date: 2026-10-03 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b1c2d3e4f50"
down_revision: Union[str, None] = "4e486bc5d2f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Quotation idempotency: one quotation per client-generated intake key.
    op.add_column("quotations", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.create_unique_constraint("uq_quotations_idempotency_key", "quotations", ["idempotency_key"])

    # Reservation ownership.
    op.add_column("inventory_units", sa.Column("reserved_by_user_id", sa.Integer(), nullable=True))
    op.add_column("inventory_units", sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_inventory_units_reserved_by_user_id_users",
        "inventory_units",
        "users",
        ["reserved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Inventory status is a closed set (existing rows are already within it).
    op.create_check_constraint(
        "ck_inventory_units_status",
        "inventory_units",
        "status IN ('AVAILABLE', 'RESERVED', 'SOLD', 'SERVICE', 'IN_TRANSIT')",
    )

    # Customer names can never be blank, even if application validation is bypassed.
    op.create_check_constraint(
        "ck_customers_full_name_not_blank",
        "customers",
        "length(btrim(full_name)) >= 2",
    )


def downgrade() -> None:
    op.drop_constraint("ck_customers_full_name_not_blank", "customers", type_="check")
    op.drop_constraint("ck_inventory_units_status", "inventory_units", type_="check")
    op.drop_constraint("fk_inventory_units_reserved_by_user_id_users", "inventory_units", type_="foreignkey")
    op.drop_column("inventory_units", "reserved_at")
    op.drop_column("inventory_units", "reserved_by_user_id")
    op.drop_constraint("uq_quotations_idempotency_key", "quotations", type_="unique")
    op.drop_column("quotations", "idempotency_key")
