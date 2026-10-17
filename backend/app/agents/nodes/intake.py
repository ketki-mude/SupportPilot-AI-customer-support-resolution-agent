"""Intake: screen the message, classify the intent, and work out which order it is about."""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agents.guardrails import detect_injection, redact_pii
from app.agents.llm import LLMError
from app.agents.nodes.base import set_ticket, step
from app.agents.prompts import intake_messages
from app.agents.runtime import AgentDeps
from app.agents.state import (
    PER_REQUEST_DEFAULTS,
    AgentState,
    ChatTurn,
    Clarification,
    EscalationCause,
    OrderCandidate,
)
from app.agents.tools.commerce import CommerceError
from app.db.models import MessageSender, TicketCategory
from app.schemas.agent import IntakeOutput
from app.schemas.commerce import CustomerOrders
from app.services import ticket_service
from app.services.audit_service import AuditStatus

NEEDS_ORDER = frozenset(
    {
        TicketCategory.REFUND,
        TicketCategory.REPLACEMENT,
        TicketCategory.MISSING_ORDER,
        TicketCategory.CANCELLATION,
    }
)
_ORDER_ID = re.compile(r"\bORD-\d{4,10}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class OrderSelection:
    order_id: str | None = None
    clarification: Clarification | None = None
    candidates: list[OrderCandidate] = field(default_factory=list)


@step("intake")
async def intake(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    ticket_id = uuid.UUID(state.ticket_id)
    history = await ticket_service.list_messages(deps.session, ticket_id)
    turns = [ChatTurn(sender=m.sender, content=redact_pii(m.content)) for m in history]
    fresh: dict[str, Any] = {**PER_REQUEST_DEFAULTS, "messages": turns}

    flags = detect_injection(latest_customer_text(turns))
    if flags:
        await deps.audit.record(
            ticket_id=ticket_id,
            action="injection_suspected",
            status=AuditStatus.FLAGGED,
            input_data={"patterns": flags},
        )
        return {**fresh, "injection_flags": flags, "escalation": EscalationCause.INJECTION}

    try:
        orders: CustomerOrders = await deps.tools["list_my_orders"].ainvoke({})
        result = await deps.llm.generate(
            IntakeOutput, intake_messages(turns, orders.orders), deps.usage
        )
    except CommerceError as exc:
        return {**fresh, "escalation": EscalationCause.TOOL_FAILURE, "errors": [str(exc)]}
    except LLMError as exc:
        return {**fresh, "escalation": EscalationCause.MODEL_FAILURE, "errors": [str(exc)]}

    selection = select_order(
        result.intent, mentioned_order_ids(turns), result.matching_order_ids, orders
    )
    await set_ticket(
        deps, category=result.intent, summary=result.summary, order_id=selection.order_id
    )
    return {
        **fresh,
        "intent": result.intent,
        "order_id": selection.order_id,
        "clarification": selection.clarification,
        "candidates": selection.candidates,
    }


def latest_customer_text(turns: list[ChatTurn]) -> str:
    return next((t.content for t in reversed(turns) if t.sender is MessageSender.CUSTOMER), "")


def mentioned_order_ids(turns: list[ChatTurn]) -> list[str]:
    """Order numbers in the most recent customer message that names any."""
    for turn in reversed(turns):
        if turn.sender is MessageSender.CUSTOMER and (found := _ORDER_ID.findall(turn.content)):
            return list(dict.fromkeys(order_id.upper() for order_id in found))
    return []


def select_order(
    intent: TicketCategory,
    mentioned: list[str],
    model_matches: list[str],
    orders: CustomerOrders,
) -> OrderSelection:
    """Decide which order the request is about, or what to ask the customer.

    An order number the customer typed is used as-is, even if it is not theirs: the policy gate
    then denies it and the attempt is audited. An order the model matched from a description
    counts only if it is in the customer's own list, so the model can never pick someone else's.
    """
    own = {order.order_id: order for order in orders.orders}
    if len(mentioned) == 1:
        return OrderSelection(order_id=mentioned[0])
    if mentioned:
        listed = [OrderCandidate.from_order(own[i]) for i in mentioned if i in own]
        kind = Clarification.AMBIGUOUS if listed else Clarification.UNMATCHED
        return OrderSelection(clarification=kind, candidates=listed)

    verified = [order_id for order_id in dict.fromkeys(model_matches) if order_id in own]
    if len(verified) == 1:
        return OrderSelection(order_id=verified[0])
    if intent not in NEEDS_ORDER:
        return OrderSelection()
    if verified:
        return OrderSelection(
            clarification=Clarification.AMBIGUOUS,
            candidates=[OrderCandidate.from_order(own[i]) for i in verified],
        )
    return OrderSelection(clarification=Clarification.UNMATCHED)
