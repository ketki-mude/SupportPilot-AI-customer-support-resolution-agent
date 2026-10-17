"""Use clock_timestamp() for created_at and updated_at so in-transaction inserts keep their order.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP_COLUMNS = (
    ("users", "created_at"),
    ("tickets", "created_at"),
    ("tickets", "updated_at"),
    ("messages", "created_at"),
    ("approvals", "created_at"),
    ("audit_logs", "created_at"),
    ("agent_runs", "created_at"),
    ("policy_chunks", "created_at"),
)


def _set_default(expression: str) -> None:
    for table, column in TIMESTAMP_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {expression}")


def upgrade() -> None:
    _set_default("clock_timestamp()")


def downgrade() -> None:
    _set_default("now()")
