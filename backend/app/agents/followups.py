"""What happens when a customer writes again on an existing ticket.

- open: the conversation continues and the agent runs again.
- awaiting approval: a fixed reply; no model call and no agent run. The message stays on the
  ticket for the supervisor reviewing it.
- resolved, rejected, or escalated: the ticket is not reopened. The message is saved, the ticket
  is flagged `customer_replied` for staff, and a fixed reply is sent once.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import replies
from app.db.models import Message, MessageSender, Ticket, TicketStatus
from app.services import ticket_service


class FollowUpKind(StrEnum):
    CONTINUE = "continue"
    AWAITING_APPROVAL = "awaiting_approval"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class FollowUp:
    kind: FollowUpKind
    customer_message: Message
    reply: Message | None

    @property
    def runs_agent(self) -> bool:
        return self.kind is FollowUpKind.CONTINUE


async def receive_follow_up(session: AsyncSession, ticket: Ticket, content: str) -> FollowUp:
    """Save the customer's message and decide how to respond. The caller commits."""
    message = await ticket_service.add_message(session, ticket.id, MessageSender.CUSTOMER, content)
    if ticket.status is TicketStatus.OPEN:
        return FollowUp(FollowUpKind.CONTINUE, message, None)
    if ticket.status is TicketStatus.AWAITING_APPROVAL:
        reply = await ticket_service.add_message(
            session, ticket.id, MessageSender.AGENT, replies.AWAITING_APPROVAL_FOLLOW_UP
        )
        return FollowUp(FollowUpKind.AWAITING_APPROVAL, message, reply)

    first_since_closed = not ticket.customer_replied
    ticket.customer_replied = True
    closed_reply = (
        await ticket_service.add_message(
            session, ticket.id, MessageSender.AGENT, replies.CLOSED_TICKET_FOLLOW_UP
        )
        if first_since_closed
        else None
    )
    await session.flush()
    return FollowUp(FollowUpKind.CLOSED, message, closed_reply)
