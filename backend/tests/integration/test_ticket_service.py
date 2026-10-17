"""Ticket service: creation, messages, and status transitions."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MessageSender, Ticket, TicketCategory, TicketPriority, TicketStatus
from app.schemas.ticket import TicketUpdate
from app.services import ticket_service
from app.services.ticket_service import InvalidTicketTransitionError, TicketNotFoundError


async def _ticket(session: AsyncSession, **kwargs: Any) -> Ticket:
    return await ticket_service.create_ticket(
        session,
        customer_id="cus_001",
        first_message="Where is my order?",
        **kwargs,
    )


async def test_create_ticket_records_the_first_message(db_session: AsyncSession) -> None:
    ticket = await _ticket(db_session, order_id="ORD-10003", category=TicketCategory.MISSING_ORDER)
    assert ticket.public_id.startswith("TCK-")
    assert ticket.status is TicketStatus.OPEN
    assert ticket.priority is TicketPriority.NORMAL
    [message] = await ticket_service.list_messages(db_session, ticket.id)
    assert (message.sender, message.content) == (MessageSender.CUSTOMER, "Where is my order?")


async def test_replacement_is_a_valid_category(db_session: AsyncSession) -> None:
    ticket = await _ticket(db_session, category=TicketCategory.REPLACEMENT)
    assert ticket.category is TicketCategory.REPLACEMENT


async def test_messages_keep_their_order_within_one_transaction(db_session: AsyncSession) -> None:
    ticket = await _ticket(db_session)
    for index in range(6):
        sender = MessageSender.AGENT if index % 2 else MessageSender.CUSTOMER
        await ticket_service.add_message(db_session, ticket.id, sender, f"message {index}")
    contents = [m.content for m in await ticket_service.list_messages(db_session, ticket.id)]
    assert contents == ["Where is my order?", *(f"message {i}" for i in range(6))]


async def test_get_missing_ticket(db_session: AsyncSession) -> None:
    with pytest.raises(TicketNotFoundError):
        await ticket_service.get_ticket(db_session, uuid.uuid4())


async def test_update_changes_only_given_fields(db_session: AsyncSession) -> None:
    ticket = await _ticket(db_session, category=TicketCategory.REFUND)
    await ticket_service.update_ticket(
        db_session, ticket, TicketUpdate(summary="Damaged item", priority=TicketPriority.HIGH)
    )
    assert (ticket.summary, ticket.priority, ticket.category) == (
        "Damaged item",
        TicketPriority.HIGH,
        TicketCategory.REFUND,
    )


@pytest.mark.parametrize(
    "path",
    [
        [TicketStatus.AWAITING_APPROVAL, TicketStatus.RESOLVED],
        [TicketStatus.AWAITING_APPROVAL, TicketStatus.REJECTED],
        [TicketStatus.AWAITING_APPROVAL, TicketStatus.ESCALATED, TicketStatus.RESOLVED],
        [TicketStatus.ESCALATED, TicketStatus.REJECTED],
        [TicketStatus.RESOLVED],
    ],
)
async def test_allowed_transitions(db_session: AsyncSession, path: list[TicketStatus]) -> None:
    ticket = await _ticket(db_session)
    for status in path:
        await ticket_service.update_ticket(db_session, ticket, TicketUpdate(status=status))
    assert ticket.status is path[-1]


@pytest.mark.parametrize(
    ("path", "forbidden"),
    [
        ([TicketStatus.RESOLVED], TicketStatus.OPEN),
        ([TicketStatus.RESOLVED], TicketStatus.ESCALATED),
        ([TicketStatus.REJECTED], TicketStatus.RESOLVED),
        ([TicketStatus.AWAITING_APPROVAL], TicketStatus.OPEN),
        ([TicketStatus.ESCALATED], TicketStatus.AWAITING_APPROVAL),
    ],
)
async def test_forbidden_transitions(
    db_session: AsyncSession, path: list[TicketStatus], forbidden: TicketStatus
) -> None:
    ticket = await _ticket(db_session)
    for status in path:
        await ticket_service.update_ticket(db_session, ticket, TicketUpdate(status=status))
    with pytest.raises(InvalidTicketTransitionError):
        await ticket_service.update_ticket(db_session, ticket, TicketUpdate(status=forbidden))
    assert ticket.status is path[-1]


async def test_setting_the_same_status_is_a_no_op(db_session: AsyncSession) -> None:
    ticket = await _ticket(db_session)
    await ticket_service.update_ticket(db_session, ticket, TicketUpdate(status=TicketStatus.OPEN))
    assert ticket.status is TicketStatus.OPEN
