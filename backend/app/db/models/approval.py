"""Supervisor approvals for risky agent actions."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A proposed action awaiting, or carrying, a supervisor decision."""

    __tablename__ = "approvals"
    __table_args__ = (
        # A decided approval always records who decided and when; a pending one never does.
        CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL)"
            " OR (status <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="decision_recorded",
        ),
        Index(
            "uq_approvals_one_pending_per_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    proposed_action: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ApprovalStatus] = mapped_column(
        str_enum(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
        index=True,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
