"""Plumbing shared by every node: events and an audit entry around each step, a commit at the
end of each step, and helpers for messaging the customer and updating the ticket."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime
from pydantic_core import to_jsonable_python

from app.agents.runtime import AgentDeps, EventType
from app.agents.state import AgentState
from app.schemas.ticket import MessageRead, TicketRead
from app.services.audit_service import AuditStatus

NodeBody = Callable[[AgentState, AgentDeps], Awaitable[dict[str, Any]]]


class Node(Protocol):
    """A LangGraph node that receives the run's dependencies as its runtime context."""

    async def __call__(
        self, state: AgentState, *, runtime: Runtime[AgentDeps]
    ) -> dict[str, Any]: ...


# Small, non-sensitive fields worth showing in the audit trail and the live trace.
_SUMMARY_FIELDS: dict[str, tuple[str, ...] | None] = {
    "intent": None,
    "order_id": None,
    "clarification": None,
    "clarification_turns": None,
    "escalation": None,
    "injection_flags": None,
    "proposal": ("action", "skus", "confidence"),
    "decision": ("decision", "action", "rule", "amount"),
    "approval": ("approval_id", "status"),
    "result": ("action", "reference", "amount", "payment_action"),
}


def step(name: str) -> Callable[[NodeBody], Node]:
    """Wrap a node body with events, auditing, and a commit.

    The state update is stored as plain JSON so checkpoints never hold Python objects; LangGraph
    rebuilds the typed state from it for the next node. Pausing for approval is not a failure.
    """

    def decorate(body: NodeBody) -> Node:
        async def node(state: AgentState, *, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
            deps = runtime.context
            ticket_id = uuid.UUID(state.ticket_id)
            deps.events.publish(EventType.STEP_STARTED, step=name)
            try:
                update = await body(state, deps)
                await deps.session.commit()
            except GraphBubbleUp:
                raise
            except Exception as exc:
                await deps.session.rollback()
                await deps.audit.record(
                    ticket_id=ticket_id,
                    action=f"step:{name}",
                    status=AuditStatus.FAILURE,
                    output_data={"error": type(exc).__name__, "message": str(exc)},
                )
                deps.events.publish(EventType.STEP_FINISHED, step=name, status="failed")
                raise
            payload: dict[str, Any] = to_jsonable_python(update)
            detail = summarize(payload)
            await deps.audit.record(
                ticket_id=ticket_id,
                action=f"step:{name}",
                status=AuditStatus.SUCCESS,
                output_data=detail,
            )
            deps.events.publish(EventType.STEP_FINISHED, step=name, status="ok", detail=detail)
            return payload

        node.__name__ = name
        return node

    return decorate


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    """The audit-worthy subset of a state update."""
    summary: dict[str, Any] = {}
    for key, fields in _SUMMARY_FIELDS.items():
        value = payload.get(key)
        if value in (None, [], {}):
            continue
        summary[key] = (
            {f: value.get(f) for f in fields} if fields and isinstance(value, dict) else value
        )
    return summary


async def say(deps: AgentDeps, text: str) -> MessageRead:
    """Message the customer on the ticket and push it to live subscribers."""
    message: MessageRead = await deps.tools["notify_customer"].ainvoke({"message": text})
    deps.events.publish(
        EventType.MESSAGE,
        message_id=str(message.id),
        sender=message.sender.value,
        content=message.content,
    )
    return message


async def set_ticket(deps: AgentDeps, **changes: Any) -> TicketRead:
    """Update the ticket through the audited tool; omitted fields stay as they are."""
    ticket: TicketRead = await deps.tools["update_ticket"].ainvoke(to_jsonable_python(changes))
    return ticket
