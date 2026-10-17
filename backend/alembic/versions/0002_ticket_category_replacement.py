"""Allow the replacement ticket category.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_tickets_ticket_category"
WITH_REPLACEMENT = (
    "category IN ('refund', 'replacement', 'missing_order', 'cancellation', 'billing', 'other')"
)
WITHOUT_REPLACEMENT = "category IN ('refund', 'missing_order', 'cancellation', 'billing', 'other')"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "tickets", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "tickets", WITH_REPLACEMENT)


def downgrade() -> None:
    # Replacement tickets fall back to the refund category they were split out of.
    op.execute("UPDATE tickets SET category = 'refund' WHERE category = 'replacement'")
    op.drop_constraint(op.f(CONSTRAINT), "tickets", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "tickets", WITHOUT_REPLACEMENT)
