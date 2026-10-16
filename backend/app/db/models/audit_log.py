"""Append-only audit trail of agent steps and human decisions.

Rows are only ever inserted. A database trigger (migration 0001) rejects UPDATE and DELETE.
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One recorded action: who did what, with which inputs, and what came back."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_ticket_id_created_at", "ticket_id", "created_at"),)

    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"))
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    output: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(32))
