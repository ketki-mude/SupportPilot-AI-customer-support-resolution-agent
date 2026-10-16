"""Per-run usage records for the agent: tokens, cost, and latency."""

import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One execution (or resumption) of the agent graph for a ticket."""

    __tablename__ = "agent_runs"

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal(0), server_default="0"
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[AgentRunStatus] = mapped_column(
        str_enum(AgentRunStatus, "agent_run_status"),
        default=AgentRunStatus.RUNNING,
        server_default=AgentRunStatus.RUNNING.value,
    )
