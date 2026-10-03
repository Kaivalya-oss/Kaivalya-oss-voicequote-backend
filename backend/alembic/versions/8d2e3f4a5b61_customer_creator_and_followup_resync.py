"""customers.created_by_user_id + resync leads.next_followup_date

* customers.created_by_user_id records which salesperson registered a customer (used for
  scoped reads). Existing rows are backfilled from the CUSTOMER_CREATED audit entries.

Before this release next_followup_date was set to whichever follow-up was created last and
never updated on reschedule / completion. Recompute it as the earliest SCHEDULED follow-up
(NULL when none). Only this derived column is touched.

Revision ID: 8d2e3f4a5b61
Revises: 7b1c2d3e4f50
Create Date: 2026-10-03 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d2e3f4a5b61"
down_revision: Union[str, None] = "7b1c2d3e4f50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_customers_created_by_user_id_users", "customers", "users",
        ["created_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_customers_created_by_user_id", "customers", ["created_by_user_id"])
    op.execute(
        """
        UPDATE customers c
        SET created_by_user_id = a.user_id
        FROM (
            SELECT DISTINCT ON (entity_id) entity_id, user_id
            FROM audit_logs
            WHERE action = 'CUSTOMER_CREATED' AND entity_type = 'CUSTOMER' AND user_id IS NOT NULL
            ORDER BY entity_id, id
        ) a
        WHERE a.entity_id = c.id AND c.created_by_user_id IS NULL
          AND EXISTS (SELECT 1 FROM users u WHERE u.id = a.user_id)
        """
    )
    op.execute(
        """
        UPDATE leads l
        SET next_followup_date = (
            SELECT MIN(f.scheduled_at)
            FROM followups f
            WHERE f.lead_id = l.id AND f.status = 'SCHEDULED'
        )
        WHERE l.next_followup_date IS DISTINCT FROM (
            SELECT MIN(f.scheduled_at)
            FROM followups f
            WHERE f.lead_id = l.id AND f.status = 'SCHEDULED'
        )
        """
    )


def downgrade() -> None:
    # next_followup_date is derived data; its previous (inconsistent) values are not restored.
    op.drop_index("ix_customers_created_by_user_id", table_name="customers")
    op.drop_constraint("fk_customers_created_by_user_id_users", "customers", type_="foreignkey")
    op.drop_column("customers", "created_by_user_id")
