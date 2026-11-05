"""tenant schema additions for multi-tenant SaaS

This chain defines the schema of every showroom database. All changes here are additive.

Revision ID: b2d5f8a1c604
Revises: a1c4e7d2f903
Create Date: 2026-11-06 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d5f8a1c604"
down_revision: Union[str, None] = "a1c4e7d2f903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Links a showroom's staff row to the central account, keeping existing foreign keys to users.id.
    op.add_column("users", sa.Column("central_user_id", sa.Uuid(), nullable=True))
    op.create_index("ix_users_central_user_id", "users", ["central_user_id"], unique=True)

    # Typed snapshot of each vehicle option, alongside the existing details_json.
    op.add_column("quotation_items", sa.Column("vehicle_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_quotation_items_vehicle_id", "quotation_items", "vehicle_variants", ["vehicle_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("quotation_items", sa.Column("model_name_snapshot", sa.String(150), nullable=True))
    op.add_column("quotation_items", sa.Column("variant_snapshot", sa.String(150), nullable=True))
    for column in ("ex_showroom", "rto", "insurance", "accessories", "other_charges", "discount", "total"):
        op.add_column("quotation_items", sa.Column(column, sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "quotation_items",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("quotation_items", "created_at")
    for column in ("total", "discount", "other_charges", "accessories", "insurance", "rto", "ex_showroom"):
        op.drop_column("quotation_items", column)
    op.drop_column("quotation_items", "variant_snapshot")
    op.drop_column("quotation_items", "model_name_snapshot")
    op.drop_constraint("fk_quotation_items_vehicle_id", "quotation_items", type_="foreignkey")
    op.drop_column("quotation_items", "vehicle_id")
    op.drop_index("ix_users_central_user_id", table_name="users")
    op.drop_column("users", "central_user_id")
