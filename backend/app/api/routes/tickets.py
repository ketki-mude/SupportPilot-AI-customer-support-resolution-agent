"""Tickets: staff see and search all of them; customers see their own."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.commerce import CommerceClient, CommerceError
from app.api.deps import AgentRunnerDep, CurrentUser, SessionDep, visible_customer_id
from app.db.models import AgentRun, Approval, AuditLog, Ticket, TicketStatus
from app.schemas.approval import ApprovalRead
from app.schemas.common import Page
from app.schemas.errors import ErrorResponse
from app.schemas.ticket import (
    AuditEntryRead,
    CustomerContext,
    CustomerSummary,
    MessageRead,
    OrderBrief,
    TicketDetail,
    TicketListItem,
    TicketRead,
    TicketUsage,
)
from app.services import ticket_service

router = APIRouter(prefix="/tickets", tags=["tickets"], responses={401: {"model": ErrorResponse}})

RECENT_ORDERS = 5


@router.get("")
async def list_tickets(
    user: CurrentUser,
    session: SessionDep,
    runner: AgentRunnerDep,
    status: TicketStatus | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[TicketListItem]:
    tickets, total = await ticket_service.list_tickets(
        session,
        customer_id=visible_customer_id(user),
        status=status,
        query=q,
        page=page,
        page_size=page_size,
    )
    names = await _customer_names(runner.commerce, {t.customer_id for t in tickets})
    items = [
        TicketListItem(
            **TicketRead.model_validate(t).model_dump(), customer_name=names.get(t.customer_id)
        )
        for t in tickets
    ]
    return Page(items=items, total=total, page=page, page_size=page_size)


async def _customer_names(commerce: CommerceClient, customer_ids: set[str]) -> dict[str, str]:
    """Names for the list view. A lookup failure only hides the name, never the tickets."""

    async def name(customer_id: str) -> tuple[str, str | None]:
        try:
            return customer_id, (await commerce.get_customer(customer_id)).name
        except CommerceError:
            return customer_id, None

    pairs = await asyncio.gather(*(name(c) for c in customer_ids))
    return {customer_id: found for customer_id, found in pairs if found}


@router.get("/{ticket_id}", responses={404: {"model": ErrorResponse}})
async def get_ticket(
    ticket_id: uuid.UUID, user: CurrentUser, session: SessionDep, runner: AgentRunnerDep
) -> TicketDetail:
    """Messages for everyone; approvals, the agent trace, and customer context for staff."""
    customer_id = visible_customer_id(user)
    ticket = await ticket_service.get_ticket_for(session, ticket_id, customer_id=customer_id)
    messages = [
        MessageRead.model_validate(m)
        for m in await ticket_service.list_messages(session, ticket.id)
    ]
    detail = TicketDetail(ticket=TicketRead.model_validate(ticket), messages=messages)
    if customer_id is not None:
        return detail

    approvals = await session.scalars(
        select(Approval).where(Approval.ticket_id == ticket.id).order_by(Approval.created_at)
    )
    trace = await session.scalars(
        select(AuditLog).where(AuditLog.ticket_id == ticket.id).order_by(AuditLog.created_at)
    )
    detail.approvals = [ApprovalRead.model_validate(a) for a in approvals]
    detail.trace = [AuditEntryRead.model_validate(e) for e in trace]
    detail.usage = await _usage(session, ticket.id)
    try:
        detail.customer_context = await _customer_context(runner.commerce, ticket)
    except CommerceError as exc:
        detail.customer_context_error = str(exc)
    return detail


async def _usage(session: AsyncSession, ticket_id: uuid.UUID) -> TicketUsage:
    runs, input_tokens, output_tokens, cost, latency = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(AgentRun.input_tokens), 0),
                func.coalesce(func.sum(AgentRun.output_tokens), 0),
                func.coalesce(func.sum(AgentRun.cost_usd), 0),
                func.coalesce(func.sum(AgentRun.latency_ms), 0),
            ).where(AgentRun.ticket_id == ticket_id)
        )
    ).one()
    last = await session.scalar(
        select(AgentRun.status)
        .where(AgentRun.ticket_id == ticket_id)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    return TicketUsage(
        runs=runs,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=latency,
        last_run_status=last.value if last else None,
    )


async def _customer_context(commerce: CommerceClient, ticket: Ticket) -> CustomerContext:
    customer = await commerce.get_customer(ticket.customer_id)
    orders = await commerce.list_orders(ticket.customer_id)
    order = next((o for o in orders if o.order_id == ticket.order_id), None)
    recent = sorted(orders, key=lambda o: o.placed_at, reverse=True)[:RECENT_ORDERS]
    return CustomerContext(
        customer=CustomerSummary.model_validate(customer.model_dump()),
        order=order,
        recent_orders=[
            OrderBrief(
                order_id=o.order_id,
                status=o.status,
                total=f"{o.total:.2f}",
                placed_at=o.placed_at,
                items=[i.name for i in o.items],
            )
            for o in recent
        ],
    )
