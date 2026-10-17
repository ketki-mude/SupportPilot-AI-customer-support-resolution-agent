"""Ticket lifecycle: create, load, add messages, and update with checked status transitions.

Functions flush but never commit; the caller owns the transaction.
"""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.models import (
    Message,
    MessageSender,
    Ticket,
    TicketCategory,
    TicketPriority,
    TicketStatus,
)
from app.schemas.ticket import TicketUpdate

_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.OPEN: frozenset(
        {
            TicketStatus.AWAITING_APPROVAL,
            TicketStatus.RESOLVED,
            TicketStatus.ESCALATED,
            TicketStatus.REJECTED,
        }
    ),
    TicketStatus.AWAITING_APPROVAL: frozenset(
        {TicketStatus.RESOLVED, TicketStatus.REJECTED, TicketStatus.ESCALATED}
    ),
    # A human picks up escalated tickets and closes them out.
    TicketStatus.ESCALATED: frozenset({TicketStatus.RESOLVED, TicketStatus.REJECTED}),
    TicketStatus.RESOLVED: frozenset(),
    TicketStatus.REJECTED: frozenset(),
}


class TicketNotFoundError(NotFoundError):
    code = "ticket_not_found"


class InvalidTicketTransitionError(ConflictError):
    code = "invalid_ticket_transition"


async def create_ticket(
    session: AsyncSession,
    *,
    customer_id: str,
    first_message: str,
    order_id: str | None = None,
    category: TicketCategory | None = None,
    priority: TicketPriority = TicketPriority.NORMAL,
) -> Ticket:
    """Open a ticket and record the customer's first message."""
    ticket = Ticket(
        customer_id=customer_id, order_id=order_id, category=category, priority=priority
    )
    session.add(ticket)
    await session.flush()
    await add_message(session, ticket.id, MessageSender.CUSTOMER, first_message)
    return ticket


async def get_ticket(session: AsyncSession, ticket_id: uuid.UUID) -> Ticket:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise TicketNotFoundError("Ticket not found")
    return ticket


async def get_ticket_for(
    session: AsyncSession, ticket_id: uuid.UUID, *, customer_id: str | None
) -> Ticket:
    """Load a ticket the caller may see: staff (customer_id None) see all, customers their own.

    Someone else's ticket looks exactly like a missing one, so ids cannot be probed.
    """
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None or (customer_id is not None and ticket.customer_id != customer_id):
        raise TicketNotFoundError("Ticket not found")
    return ticket


async def list_tickets(
    session: AsyncSession,
    *,
    customer_id: str | None,
    status: TicketStatus | None,
    query: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Ticket], int]:
    """Newest activity first. `query` matches the ticket number, order, customer, or summary."""
    conditions = []
    if customer_id is not None:
        conditions.append(Ticket.customer_id == customer_id)
    if status is not None:
        conditions.append(Ticket.status == status)
    if query:
        pattern = f"%{query.strip()}%"
        conditions.append(
            or_(
                Ticket.public_id.ilike(pattern),
                Ticket.order_id.ilike(pattern),
                Ticket.customer_id.ilike(pattern),
                Ticket.summary.ilike(pattern),
            )
        )
    total = await session.scalar(select(func.count()).select_from(Ticket).where(*conditions))
    rows = await session.scalars(
        select(Ticket)
        .where(*conditions)
        .order_by(Ticket.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows), total or 0


async def add_message(
    session: AsyncSession, ticket_id: uuid.UUID, sender: MessageSender, content: str
) -> Message:
    message = Message(ticket_id=ticket_id, sender=sender, content=content)
    session.add(message)
    await session.flush()
    return message


async def list_messages(session: AsyncSession, ticket_id: uuid.UUID) -> list[Message]:
    """The conversation in the order it happened."""
    result = await session.scalars(
        select(Message).where(Message.ticket_id == ticket_id).order_by(Message.created_at)
    )
    return list(result)


async def update_ticket(session: AsyncSession, ticket: Ticket, changes: TicketUpdate) -> Ticket:
    """Apply the provided fields. Status may only move along allowed transitions."""
    if changes.status is not None and changes.status is not ticket.status:
        if changes.status not in _TRANSITIONS[ticket.status]:
            raise InvalidTicketTransitionError(
                f"A ticket cannot move from {ticket.status.value} to {changes.status.value}"
            )
        ticket.status = changes.status
    for field in ("category", "priority", "summary", "order_id"):
        value = getattr(changes, field)
        if value is not None:
            setattr(ticket, field, value)
    await session.flush()
    return ticket
