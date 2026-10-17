"""Supervisor approvals: open one per risky action, record the decision exactly once."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.models import Approval, ApprovalStatus


class ApprovalNotFoundError(NotFoundError):
    code = "approval_not_found"


class ApprovalAlreadyDecidedError(ConflictError):
    code = "approval_already_decided"


async def open_approval(
    session: AsyncSession, ticket_id: uuid.UUID, proposed_action: dict[str, Any]
) -> Approval:
    """Return the ticket's pending approval, creating it if there is none."""
    existing = await session.scalar(
        select(Approval).where(
            Approval.ticket_id == ticket_id, Approval.status == ApprovalStatus.PENDING
        )
    )
    if existing is not None:
        return existing
    approval = Approval(ticket_id=ticket_id, proposed_action=proposed_action)
    session.add(approval)
    await session.flush()
    return approval


async def get_approval(session: AsyncSession, approval_id: uuid.UUID) -> Approval:
    approval = await session.get(Approval, approval_id, populate_existing=True)
    if approval is None:
        raise ApprovalNotFoundError("Approval not found")
    return approval


async def decide(
    session: AsyncSession,
    approval_id: uuid.UUID,
    *,
    approve: bool,
    decided_by: uuid.UUID,
    note: str | None,
) -> Approval:
    """Record a supervisor's decision. The row is locked, so two clicks cannot both decide."""
    approval = await session.scalar(
        select(Approval).where(Approval.id == approval_id).with_for_update()
    )
    if approval is None:
        raise ApprovalNotFoundError("Approval not found")
    if approval.status is not ApprovalStatus.PENDING:
        raise ApprovalAlreadyDecidedError(f"This approval was already {approval.status.value}")
    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    approval.decided_by = decided_by
    approval.decided_at = datetime.now(UTC)
    approval.note = note
    await session.flush()
    return approval
