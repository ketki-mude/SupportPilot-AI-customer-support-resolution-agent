"""Allow messages from support staff, who now reply on tickets the agent handed over.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_messages_message_sender"
WITH_STAFF = "sender IN ('customer', 'agent', 'supervisor', 'staff', 'system')"
WITHOUT_STAFF = "sender IN ('customer', 'agent', 'supervisor', 'system')"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "messages", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "messages", WITH_STAFF)


def downgrade() -> None:
    op.execute("UPDATE messages SET sender = 'supervisor' WHERE sender = 'staff'")
    op.drop_constraint(op.f(CONSTRAINT), "messages", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "messages", WITHOUT_STAFF)
