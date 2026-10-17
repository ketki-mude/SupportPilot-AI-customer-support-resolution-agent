"""Supervisor approvals: the queue, and the decision that resumes the paused agent."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import AgentRunnerDep, SessionDep, StaffUser, SupervisorUser
from app.core.errors import ConflictError
from app.db.models import Approval, ApprovalStatus, Ticket, TicketStatus
from app.schemas.approval import ApprovalDecisionRequest, ApprovalListItem, ApprovalRead
from app.schemas.common import Page
from app.schemas.errors import ErrorResponse
from app.services import approval_service, ticket_service

router = APIRouter(
    prefix="/approvals",
    tags=["approvals"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)


@router.get("")
async def list_approvals(
    _: StaffUser,
    session: SessionDep,
    status: ApprovalStatus | None = ApprovalStatus.PENDING,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ApprovalListItem]:
    """Oldest first, so the longest-waiting customer is at the top."""
    conditions = [Approval.status == status] if status else []
    total = await session.scalar(select(func.count()).select_from(Approval).where(*conditions))
    rows = await session.execute(
        select(Approval, Ticket.public_id)
        .join(Ticket, Ticket.id == Approval.ticket_id)
        .where(*conditions)
        .order_by(Approval.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        ApprovalListItem(**ApprovalRead.model_validate(a).model_dump(), ticket_public_id=public_id)
        for a, public_id in rows
    ]
    return Page(items=items, total=total or 0, page=page, page_size=page_size)


@router.post(
    "/{approval_id}/decision",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def decide(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    user: SupervisorUser,
    session: SessionDep,
    runner: AgentRunnerDep,
) -> ApprovalRead:
    """Approve or reject, then resume the agent. A second decision on the same approval is a 409."""
    approval = await approval_service.decide(
        session,
        approval_id,
        approve=body.decision == "approve",
        decided_by=user.id,
        note=body.note,
    )
    ticket = await ticket_service.get_ticket(session, approval.ticket_id)
    if ticket.status is not TicketStatus.AWAITING_APPROVAL:
        raise ConflictError("This ticket is not waiting for approval", code="ticket_not_awaiting")
    await session.commit()
    await runner.audit.record(
        ticket_id=ticket.id,
        actor=str(user.id),
        action="approval_decision",
        status=approval.status,
        input_data={"approval_id": str(approval.id), "note": body.note},
        output_data=approval.proposed_action,
    )
    runner.resume(ticket.id, ticket.customer_id)
    return ApprovalRead.model_validate(approval)
