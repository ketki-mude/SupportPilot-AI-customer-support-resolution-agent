"""The agent's graph state. One thread per ticket; the checkpointer persists it between runs."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import ApprovalStatus, MessageSender, TicketCategory
from app.schemas.agent import PolicyDecision, PolicySnippet, Proposal, ProposedAction
from app.schemas.commerce import Customer, Order, OrderStatus


class ChatTurn(BaseModel):
    """One message as the model sees it: PII already redacted."""

    sender: MessageSender
    content: str


class OrderCandidate(BaseModel):
    """A short description of one of the customer's orders, used to ask which one they mean."""

    order_id: str
    status: OrderStatus
    items: list[str]
    placed_at: datetime
    delivered_at: datetime | None

    @classmethod
    def from_order(cls, order: Order) -> "OrderCandidate":
        return cls(
            order_id=order.order_id,
            status=order.status,
            items=[item.name for item in order.items],
            placed_at=order.placed_at,
            delivered_at=order.delivered_at,
        )


class Clarification(StrEnum):
    AMBIGUOUS = "ambiguous"  # several of the customer's orders fit
    UNMATCHED = "unmatched"  # none fit, or the order number was not found


class EscalationCause(StrEnum):
    POLICY = "policy"
    INJECTION = "injection"
    TOOL_FAILURE = "tool_failure"
    MODEL_FAILURE = "model_failure"
    UNAUTHORIZED_ACTION = "unauthorized_action"
    CLARIFICATION_LIMIT = "clarification_limit"


class ApprovalOutcome(BaseModel):
    approval_id: str
    status: ApprovalStatus


class ActionResult(BaseModel):
    """What the executor did, for the customer message and the audit trail."""

    action: ProposedAction
    reference: str | None = None
    amount: Decimal | None = None
    payment_action: Literal["refunded", "voided"] | None = None


class AgentState(BaseModel):
    """Fields persist across runs on the same ticket; intake resets the per-request ones."""

    ticket_id: str
    customer_id: str
    messages: list[ChatTurn] = Field(default_factory=list)
    intent: TicketCategory | None = None
    injection_flags: list[str] = Field(default_factory=list)
    order_id: str | None = None
    candidates: list[OrderCandidate] = Field(default_factory=list)
    clarification: Clarification | None = None
    clarification_turns: int = 0
    customer: Customer | None = None
    order: Order | None = None
    policy_snippets: list[PolicySnippet] = Field(default_factory=list)
    proposal: Proposal | None = None
    decision: PolicyDecision | None = None
    approval: ApprovalOutcome | None = None
    result: ActionResult | None = None
    escalation: EscalationCause | None = None
    errors: list[str] = Field(default_factory=list)

    @property
    def owns_order(self) -> bool:
        return self.order is None or self.order.customer_id == self.customer_id


# Cleared at the start of every run so nothing from an earlier request leaks into this one.
PER_REQUEST_DEFAULTS: dict[str, object] = {
    "intent": None,
    "injection_flags": [],
    "order_id": None,
    "candidates": [],
    "clarification": None,
    "customer": None,
    "order": None,
    "policy_snippets": [],
    "proposal": None,
    "decision": None,
    "approval": None,
    "result": None,
    "escalation": None,
    "errors": [],
}
