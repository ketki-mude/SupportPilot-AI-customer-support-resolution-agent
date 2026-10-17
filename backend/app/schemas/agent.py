"""What the agent proposes and what the policy engine decides."""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.ticket import TicketCategory
from app.rag.chunking import citation


class ProposedAction(StrEnum):
    REFUND = "refund"
    REPLACEMENT = "replacement"
    CANCEL = "cancel"
    INFO = "info"
    ESCALATE = "escalate"


class Decision(StrEnum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_APPROVAL = "needs_approval"
    DENY = "deny"
    ESCALATE = "escalate"


class Rule(StrEnum):
    """Identifiers for every rule the policy engine can apply, used in audits and the UI."""

    OWNERSHIP_MISMATCH = "ownership_mismatch"
    FRAUD_FLAG = "fraud_flag"
    UNKNOWN_INTENT = "unknown_intent"
    LOW_CONFIDENCE = "low_confidence"
    AGENT_REQUESTED_ESCALATION = "agent_requested_escalation"
    INFO_ONLY = "info_only"
    ORDER_REQUIRED = "order_required"
    NOT_DELIVERED = "not_delivered"
    DELIVERY_OVERDUE = "delivery_overdue"
    DELIVERY_DATE_MISSING = "delivery_date_missing"
    REMEDY_ALREADY_APPLIED = "remedy_already_applied"
    OUTSIDE_WINDOW = "outside_window"
    UNKNOWN_ITEM = "unknown_item"
    AMOUNT_MISMATCH = "amount_mismatch"
    WITHIN_AUTO_APPROVE_LIMIT = "within_auto_approve_limit"
    NEEDS_SUPERVISOR = "needs_supervisor"
    OVER_SUPERVISOR_LIMIT = "over_supervisor_limit"
    CANCEL_UNSHIPPED = "cancel_unshipped"
    CANCEL_AFTER_SHIPMENT = "cancel_after_shipment"
    CANCEL_AFTER_DELIVERY = "cancel_after_delivery"
    ALREADY_CANCELLED = "already_cancelled"


class Citation(BaseModel):
    source: str
    section: str


class PolicySnippet(BaseModel):
    """A retrieved policy section and how closely it matched the question."""

    model_config = ConfigDict(frozen=True)

    source: str
    section: str
    content: str
    score: float

    @property
    def citation(self) -> str:
        return citation(self.source, self.section)


class Proposal(BaseModel):
    """The agent's structured suggestion. It never sets money on its own authority."""

    model_config = ConfigDict(frozen=True)

    action: ProposedAction
    skus: list[str] = Field(
        default_factory=list, description="Items the request is about; empty means the whole order"
    )
    amount: Decimal | None = Field(
        default=None, description="If given, must equal the amount computed from the order"
    )
    reason: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    citations: list[Citation] = Field(default_factory=list)
    customer_reply: str | None = Field(
        default=None, max_length=1500, description="Answer text, used only for info proposals"
    )


class IntakeOutput(BaseModel):
    """What the intake model reports about a conversation."""

    intent: TicketCategory
    matching_order_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Ids from the customer's order list that fit the description; empty if unclear",
    )
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(
        min_length=1, max_length=400, description="Two neutral sentences for staff"
    )


class ResolutionOutput(BaseModel):
    """What the resolution model proposes. It has no amount field: money comes from the order."""

    action: ProposedAction
    skus: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)
    citations: list[Citation] = Field(default_factory=list, max_length=4)
    customer_reply: str | None = Field(default=None, max_length=1200)


class PolicyDecision(BaseModel):
    """The engine's verdict. `action` is what runs if the decision allows it."""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    action: ProposedAction
    rule: Rule
    reason: str
    amount: Decimal | None = None
    skus: list[str] = Field(default_factory=list)
