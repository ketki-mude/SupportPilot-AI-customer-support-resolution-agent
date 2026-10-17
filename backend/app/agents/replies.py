"""Customer-facing messages.

Anything about money, order status, or a decision is a fixed template filled from verified
data, so the customer is never told something the system did not do. Only answers to plain
questions use model-written text.
"""

from datetime import datetime
from decimal import Decimal

from app.agents.state import (
    ActionResult,
    AgentState,
    Clarification,
    EscalationCause,
    OrderCandidate,
)
from app.db.models import ApprovalStatus
from app.schemas.agent import Decision, PolicyDecision, ProposedAction, Rule
from app.schemas.commerce import Order, OrderStatus

AWAITING_APPROVAL_FOLLOW_UP = (
    "Thanks, I've added this to your request. A supervisor is reviewing it now, and you'll get "
    "an update right here."
)
CLOSED_TICKET_FOLLOW_UP = (
    "Thanks for your message. This request is closed, but our support staff can see what you "
    "wrote. If it's about a different issue, please start a new request."
)
RUN_FAILED = (
    "Sorry, something went wrong on our side while handling your request. I've passed it to a "
    "specialist, who will follow up here."
)
_GENERIC_INFO = (
    "Thanks for your question. A member of our team will follow up if you need more help."
)


def money(amount: Decimal) -> str:
    return f"${amount:.2f}"


def day(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}, {moment.year}"


def clarification(kind: Clarification, candidates: list[OrderCandidate]) -> str:
    """Ask which order the customer means, describing their orders by item and date."""
    if kind is Clarification.AMBIGUOUS and candidates:
        lines = "\n".join(f"- {_describe(candidate)}" for candidate in candidates)
        return f"I found more than one order that could match. Which one do you mean?\n{lines}"
    return (
        "I couldn't tell which order you mean. Could you share the order number (it looks like "
        "ORD-12345), or tell me the item and roughly when you ordered it?"
    )


def approval_requested(decision: PolicyDecision, order: Order) -> str:
    amount = money(decision.amount) if decision.amount is not None else ""
    what = {
        ProposedAction.REFUND: f"a refund of {amount}",
        ProposedAction.REPLACEMENT: f"a replacement worth {amount}",
        ProposedAction.CANCEL: f"cancelling the order ({amount})",
    }.get(decision.action, "your request")
    return (
        f"Thanks for your patience. I've asked a supervisor to approve {what} for order "
        f"{order.order_id}. You'll get an update right here, usually within one business day."
    )


def outcome(state: AgentState) -> str:
    """The final message for a run that ends in the notifier."""
    decision = state.decision
    if state.approval is not None and state.approval.status is ApprovalStatus.REJECTED:
        return (
            "A supervisor reviewed your request and wasn't able to approve it. If you have more "
            "details to share, please start a new request and we'll take another look."
        )
    if decision is None:
        return _GENERIC_INFO
    if decision.decision is Decision.DENY:
        return _denied(decision, state.order)
    if state.result is not None and state.result.action is not ProposedAction.INFO:
        return _done(state.result, state.order) + _citation_line(state)
    return _information(decision, state)


def escalated(state: AgentState) -> str:
    if state.decision is not None and state.decision.rule is Rule.DELIVERY_OVERDUE:
        return (
            "Your order is past its estimated delivery date, so I've asked a specialist to look "
            "into it with the carrier. They'll follow up here."
        )
    if state.escalation is EscalationCause.CLARIFICATION_LIMIT:
        return (
            "I want to make sure we get this right, so I've passed your request to a specialist "
            "who will follow up here."
        )
    return "I've passed your request to a specialist, who will follow up here shortly."


def _done(result: ActionResult, order: Order | None) -> str:
    order_id = order.order_id if order else "your order"
    card = f" to your card ending {order.payment_method_last4}" if order else ""
    if result.action is ProposedAction.REFUND and result.amount is not None:
        return (
            f"I've refunded {money(result.amount)}{card} for order {order_id}. Most banks show the "
            "credit within 5 to 10 business days."
        )
    if result.action is ProposedAction.REPLACEMENT:
        return (
            f"A replacement for order {order_id} is on its way at no cost to you. It usually ships "
            "within two business days."
        )
    if result.action is ProposedAction.CANCEL:
        if result.payment_action == "refunded" and result.amount is not None:
            refunded = money(result.amount)
            return f"Order {order_id} is cancelled, and {refunded} has been refunded{card}."
        return f"Order {order_id} is cancelled, and the payment hold has been released."
    return _GENERIC_INFO


def _denied(decision: PolicyDecision, order: Order | None) -> str:
    order_id = order.order_id if order else "this order"
    match decision.rule:
        case Rule.OWNERSHIP_MISMATCH:
            return (
                "I couldn't find that order on your account. Please check the order number, or "
                "tell me the item and roughly when you ordered it."
            )
        case Rule.REMEDY_ALREADY_APPLIED:
            return (
                f"Order {order_id} has already been refunded or replaced, so I can't issue another "
                "refund or replacement for it."
            )
        case Rule.OUTSIDE_WINDOW if order and order.delivered_at:
            return (
                f"Order {order_id} was delivered on {day(order.delivered_at)}, which is outside "
                "our 30-day window for refunds and replacements."
            )
        case Rule.UNKNOWN_ITEM:
            return f"I couldn't match the item you mentioned to order {order_id}."
        case Rule.ORDER_REQUIRED:
            return "Could you share the order number for this request? It looks like ORD-12345."
        case _:
            return f"I'm not able to make that change to order {order_id}. {decision.reason}"


def _information(decision: PolicyDecision, state: AgentState) -> str:
    order = state.order
    match decision.rule:
        case Rule.NOT_DELIVERED if order:
            return (
                _status(order) + " Refunds and replacements can be requested once it's delivered."
            )
        case Rule.CANCEL_AFTER_DELIVERY if order and order.delivered_at:
            return (
                f"Order {order.order_id} was delivered on {day(order.delivered_at)}, so it can't "
                "be cancelled. You can still ask for a refund within 30 days of delivery."
            )
        case Rule.ALREADY_CANCELLED if order:
            when = f" on {day(order.cancelled_at)}" if order.cancelled_at else ""
            return (
                f"Order {order.order_id} was already cancelled{when}. No further action is needed."
            )
    reply = state.proposal.customer_reply if state.proposal else None
    return (reply.strip() if reply and reply.strip() else _GENERIC_INFO) + _citation_line(state)


def _status(order: Order) -> str:
    match order.status:
        case OrderStatus.PROCESSING:
            return f"Order {order.order_id} is still being prepared and hasn't shipped yet."
        case OrderStatus.SHIPPED | OrderStatus.IN_TRANSIT:
            eta = (
                f" It's expected by {day(order.estimated_delivery_at)}."
                if order.estimated_delivery_at
                else ""
            )
            tracking = (
                f" Tracking number: {order.tracking_number}." if order.tracking_number else ""
            )
            return f"Order {order.order_id} is on its way.{eta}{tracking}"
        case OrderStatus.CANCELLED:
            return f"Order {order.order_id} was cancelled."
        case _:
            return f"Order {order.order_id} is {order.status.value.replace('_', ' ')}."


def _describe(candidate: OrderCandidate) -> str:
    items = ", ".join(candidate.items)
    if candidate.delivered_at:
        return f"{candidate.order_id}: {items}, delivered {day(candidate.delivered_at)}"
    return f"{candidate.order_id}: {items}, ordered {day(candidate.placed_at)}"


def _citation_line(state: AgentState) -> str:
    if not state.proposal or not state.proposal.citations:
        return ""
    labels = {(s.source, s.section): s.citation for s in state.policy_snippets}
    cited = [
        labels[key] for c in state.proposal.citations if (key := (c.source, c.section)) in labels
    ]
    return f"\n\nPolicy: {'; '.join(cited)}" if cited else ""
