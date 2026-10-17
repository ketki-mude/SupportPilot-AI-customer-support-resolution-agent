"""Customer chat: open a ticket, send follow-ups, and stream the agent's progress."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from app.agents.followups import receive_follow_up
from app.agents.runtime import AgentEvent, EventType
from app.api.deps import (
    AgentRunnerDep,
    ChatRateLimit,
    CurrentUser,
    CustomerUser,
    SessionDep,
    visible_customer_id,
)
from app.schemas.chat import ChatMessageCreate, ChatResponse
from app.schemas.errors import ErrorResponse
from app.schemas.ticket import MessageRead, TicketRead
from app.services import ticket_service
from app.services.audit_service import AuditStatus

router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)


def _customer_id(user: CustomerUser) -> str:
    if user.customer_id is None:  # guaranteed by the users.customer_link constraint
        raise RuntimeError("Customer account without a customer id")
    return user.customer_id


@router.post("/tickets", status_code=201)
async def create_ticket(
    body: ChatMessageCreate,
    user: CustomerUser,
    session: SessionDep,
    runner: AgentRunnerDep,
    _: ChatRateLimit,
) -> ChatResponse:
    """Open a ticket with the customer's first message and start the agent on it."""
    customer_id = _customer_id(user)
    ticket = await ticket_service.create_ticket(
        session, customer_id=customer_id, first_message=body.message.strip()
    )
    await session.commit()
    messages = await ticket_service.list_messages(session, ticket.id)
    runner.start(ticket.id, customer_id)
    return ChatResponse(
        ticket=TicketRead.model_validate(ticket),
        messages=[MessageRead.model_validate(m) for m in messages],
    )


@router.post(
    "/tickets/{ticket_id}/messages", status_code=201, responses={404: {"model": ErrorResponse}}
)
async def send_message(
    ticket_id: uuid.UUID,
    body: ChatMessageCreate,
    user: CustomerUser,
    session: SessionDep,
    runner: AgentRunnerDep,
    _: ChatRateLimit,
) -> ChatResponse:
    """Add a customer message. Only open tickets run the agent again."""
    customer_id = _customer_id(user)
    ticket = await ticket_service.get_ticket_for(session, ticket_id, customer_id=customer_id)
    follow_up = await receive_follow_up(session, ticket, body.message.strip())
    await session.commit()
    await runner.audit.record(
        ticket_id=ticket.id,
        actor=str(user.id),
        action="customer_follow_up",
        status=AuditStatus.SUCCESS,
        output_data={"handling": follow_up.kind, "replied": follow_up.reply is not None},
    )
    added = [follow_up.customer_message]
    if follow_up.reply is not None:
        added.append(follow_up.reply)
        runner.events.publish(
            str(ticket.id),
            EventType.MESSAGE,
            {
                "message_id": str(follow_up.reply.id),
                "sender": follow_up.reply.sender.value,
                "content": follow_up.reply.content,
            },
        )
    if follow_up.runs_agent:
        runner.start(ticket.id, customer_id)
    return ChatResponse(
        ticket=TicketRead.model_validate(ticket),
        messages=[MessageRead.model_validate(m) for m in added],
    )


@router.get(
    "/tickets/{ticket_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, 404: {"model": ErrorResponse}},
)
async def stream(
    ticket_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    runner: AgentRunnerDep,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Server-Sent Events: step_started, step_finished, message, approval_requested, completed,
    error. Recent events are replayed first; send Last-Event-ID to resume after a reconnect."""
    await ticket_service.get_ticket_for(session, ticket_id, customer_id=visible_customer_id(user))
    await session.close()  # do not hold a database connection for the life of the stream
    events = runner.events.subscribe(str(ticket_id), after=last_event_id or 0)
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse(events: AsyncIterator[AgentEvent | None]) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    async for event in events:
        yield ": keep-alive\n\n" if event is None else event.encode()
