"""Support tickets."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Sequence, String, Text, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum


class TicketStatus(StrEnum):
    OPEN = "open"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    REJECTED = "rejected"


class TicketCategory(StrEnum):
    """The customer's intent, as classified by the agent. OTHER means unknown."""

    REFUND = "refund"
    REPLACEMENT = "replacement"
    MISSING_ORDER = "missing_order"
    CANCELLATION = "cancellation"
    BILLING = "billing"
    OTHER = "other"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


ticket_public_id_seq = Sequence("ticket_public_id_seq", start=1000, metadata=Base.metadata)


class Ticket(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One customer request. public_id (TCK-1000, TCK-1001, ...) is assigned by Postgres."""

    __tablename__ = "tickets"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012  # SQLAlchemy reads it as-is

    public_id: Mapped[str] = mapped_column(
        String(16),
        unique=True,
        server_default=text("'TCK-' || nextval('ticket_public_id_seq')"),
    )
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[TicketCategory | None] = mapped_column(
        str_enum(TicketCategory, "ticket_category")
    )
    status: Mapped[TicketStatus] = mapped_column(
        str_enum(TicketStatus, "ticket_status"),
        default=TicketStatus.OPEN,
        server_default=TicketStatus.OPEN.value,
        index=True,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        str_enum(TicketPriority, "ticket_priority"),
        default=TicketPriority.NORMAL,
        server_default=TicketPriority.NORMAL.value,
    )
    summary: Mapped[str | None] = mapped_column(Text)
    # Set when the customer writes on a closed ticket, so staff can see it needs a look.
    customer_replied: Mapped[bool] = mapped_column(default=False, server_default=false())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )
