"""Deterministic business rules. The agent proposes; this module decides.

Pure functions with no I/O. Checks run in a fixed order and the first one that applies decides.
Money is always computed from the order, never taken from the proposal.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import Settings
from app.db.models.ticket import TicketCategory
from app.schemas.agent import Decision, PolicyDecision, Proposal, ProposedAction, Rule
from app.schemas.commerce import Customer, Order, OrderItem, OrderStatus, PaymentStatus

_IN_FLIGHT = frozenset({OrderStatus.SHIPPED, OrderStatus.IN_TRANSIT})


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Facts the rules need, gathered by the agent before it asks for a decision."""

    requester_customer_id: str
    intent: TicketCategory
    customer: Customer
    order: Order | None


def evaluate(
    proposal: Proposal,
    context: PolicyContext,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> PolicyDecision:
    """Decide whether a proposal is auto-approved, needs a supervisor, is denied, or escalates.

    For auto_approve and needs_approval, `action` in the result is what will run. For deny and
    escalate it names the action that was refused or handed to a human.
    """
    now = now or datetime.now(UTC)
    order = context.order
    action = proposal.action

    if order is not None and order.customer_id != context.requester_customer_id:
        return _deny(action, Rule.OWNERSHIP_MISMATCH, "This order belongs to a different customer.")
    if context.customer.fraud_flag:
        return _escalate(action, Rule.FRAUD_FLAG, "The account is flagged for a manual review.")
    if context.intent is TicketCategory.OTHER:
        return _escalate(action, Rule.UNKNOWN_INTENT, "The request could not be classified.")
    if proposal.confidence < settings.min_confidence:
        return _escalate(
            action,
            Rule.LOW_CONFIDENCE,
            f"Agent confidence {proposal.confidence:.2f} is below {settings.min_confidence:.2f}.",
        )
    if action is ProposedAction.ESCALATE:
        return _escalate(action, Rule.AGENT_REQUESTED_ESCALATION, "The agent asked for a human.")
    if action is ProposedAction.INFO:
        return _info(Rule.INFO_ONLY, "Information only; nothing changes on the order.")
    if order is None:
        return _deny(action, Rule.ORDER_REQUIRED, "An order number is needed for this request.")
    if action is ProposedAction.CANCEL:
        return _evaluate_cancel(proposal, order, settings)
    return _evaluate_remedy(proposal, order, settings, now)


def _evaluate_remedy(
    proposal: Proposal, order: Order, settings: Settings, now: datetime
) -> PolicyDecision:
    """Refunds and replacements share every rule; a replacement is valued at its items' price."""
    action = proposal.action
    if order.status is not OrderStatus.DELIVERED:
        eta = order.estimated_delivery_at
        if order.status in _IN_FLIGHT and eta is not None and eta < now:
            return _escalate(
                action,
                Rule.DELIVERY_OVERDUE,
                f"The order was due on {eta.date().isoformat()} and has not arrived.",
            )
        return _info(
            Rule.NOT_DELIVERED,
            f"The order is {_label(order.status)}; refunds and replacements start after delivery.",
        )
    if order.remedy is not None or order.refunded_amount > 0:
        already = order.remedy.value if order.remedy else "refund"
        return _deny(action, Rule.REMEDY_ALREADY_APPLIED, f"This order already had a {already}.")
    if order.delivered_at is None:
        return _escalate(action, Rule.DELIVERY_DATE_MISSING, "The order has no delivery date.")

    days = days_since(order.delivered_at, now)
    if days > settings.refund_window_days:
        return _deny(
            action,
            Rule.OUTSIDE_WINDOW,
            f"Delivered {days} days ago; the window is {settings.refund_window_days} days.",
        )

    items = _select_items(order, proposal.skus)
    if items is None:
        return _deny(
            action, Rule.UNKNOWN_ITEM, "The request names items that are not on the order."
        )
    amount = sum((item.line_total for item in items), Decimal("0.00"))
    skus = [item.sku for item in items]
    if proposal.amount is not None and proposal.amount != amount:
        return _deny(
            action,
            Rule.AMOUNT_MISMATCH,
            f"Proposed ${proposal.amount:.2f}, but the items cost ${amount:.2f}.",
        )
    return _apply_limits(action, amount, skus, settings)


def _evaluate_cancel(proposal: Proposal, order: Order, settings: Settings) -> PolicyDecision:
    """Cancellation depends on where the order is in fulfilment."""
    action = ProposedAction.CANCEL
    if order.status is OrderStatus.PROCESSING:
        if proposal.amount is not None and proposal.amount != order.total:
            return _deny(
                action,
                Rule.AMOUNT_MISMATCH,
                f"Proposed ${proposal.amount:.2f}, but the order totals ${order.total:.2f}.",
            )
        if settings.cancel_use_refund_limits:
            return _apply_limits(action, order.total, [], settings)
        payment = "refunded" if order.payment_status is PaymentStatus.CAPTURED else "released"
        return PolicyDecision(
            decision=Decision.AUTO_APPROVE,
            action=action,
            rule=Rule.CANCEL_UNSHIPPED,
            reason=f"The order has not shipped, so it can be cancelled and the payment {payment}.",
            amount=order.total,
        )
    if order.status in _IN_FLIGHT:
        return _escalate(
            action,
            Rule.CANCEL_AFTER_SHIPMENT,
            f"The order is already {_label(order.status)}; a specialist must handle it.",
        )
    if order.status is OrderStatus.DELIVERED:
        return _info(
            Rule.CANCEL_AFTER_DELIVERY,
            f"Delivered orders cannot be cancelled; a refund can be requested within "
            f"{settings.refund_window_days} days of delivery.",
        )
    return _info(Rule.ALREADY_CANCELLED, "The order is already cancelled.")


def _apply_limits(
    action: ProposedAction, amount: Decimal, skus: list[str], settings: Settings
) -> PolicyDecision:
    """Map an amount onto the auto-approve, supervisor, and escalation bands."""
    if amount <= settings.auto_approve_max_usd:
        decision, rule, note = (
            Decision.AUTO_APPROVE,
            Rule.WITHIN_AUTO_APPROVE_LIMIT,
            "is within the automatic limit",
        )
    elif amount <= settings.supervisor_approval_max_usd:
        decision, rule, note = (
            Decision.NEEDS_APPROVAL,
            Rule.NEEDS_SUPERVISOR,
            "needs a supervisor's approval",
        )
    else:
        decision, rule, note = (
            Decision.ESCALATE,
            Rule.OVER_SUPERVISOR_LIMIT,
            "is above the supervisor limit",
        )
    return PolicyDecision(
        decision=decision,
        action=action,
        rule=rule,
        reason=f"${amount:.2f} {note}.",
        amount=amount,
        skus=skus,
    )


def days_since(moment: datetime, now: datetime) -> int:
    """Whole UTC calendar days between two instants: same day is 0, the next day is 1."""
    return (now.astimezone(UTC).date() - moment.astimezone(UTC).date()).days


def _select_items(order: Order, skus: list[str]) -> list[OrderItem] | None:
    """The items a request covers, or None if it names an item not on the order."""
    if not skus:
        return list(order.items)
    wanted = set(skus)
    if wanted - {item.sku for item in order.items}:
        return None
    return [item for item in order.items if item.sku in wanted]


def _label(status: OrderStatus) -> str:
    return status.value.replace("_", " ")


def _deny(action: ProposedAction, rule: Rule, reason: str) -> PolicyDecision:
    return PolicyDecision(decision=Decision.DENY, action=action, rule=rule, reason=reason)


def _escalate(action: ProposedAction, rule: Rule, reason: str) -> PolicyDecision:
    return PolicyDecision(decision=Decision.ESCALATE, action=action, rule=rule, reason=reason)


def _info(rule: Rule, reason: str) -> PolicyDecision:
    return PolicyDecision(
        decision=Decision.AUTO_APPROVE, action=ProposedAction.INFO, rule=rule, reason=reason
    )
