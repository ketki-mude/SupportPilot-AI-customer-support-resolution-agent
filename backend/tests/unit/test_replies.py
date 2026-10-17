"""Customer-facing messages: every template is filled from verified data."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.agents import replies
from app.agents.state import ActionResult, AgentState, ApprovalOutcome, EscalationCause
from app.db.models import ApprovalStatus
from app.schemas.agent import (
    Citation,
    Decision,
    PolicyDecision,
    PolicySnippet,
    Proposal,
    ProposedAction,
    Rule,
)
from app.schemas.commerce import Order, OrderItem, OrderStatus, PaymentStatus

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def _order(**overrides: Any) -> Order:
    values: dict[str, Any] = {
        "order_id": "ORD-10001",
        "customer_id": "cus_001",
        "status": OrderStatus.DELIVERED,
        "payment_status": PaymentStatus.CAPTURED,
        "items": [
            OrderItem(sku="SKU-1", name="Headphones", quantity=1, unit_price=Decimal("79.99"))
        ],
        "total": Decimal("79.99"),
        "currency": "USD",
        "placed_at": NOW - timedelta(days=9),
        "shipped_at": NOW - timedelta(days=7),
        "delivered_at": datetime(2026, 9, 1, tzinfo=UTC),
        "estimated_delivery_at": None,
        "cancelled_at": None,
        "tracking_number": "1Z999",
        "payment_method_last4": "4242",
        "refunded_amount": Decimal("0.00"),
        "remedy": None,
    }
    return Order(**(values | overrides))


def _decision(
    decision: Decision, rule: Rule, action: ProposedAction = ProposedAction.REFUND
) -> PolicyDecision:
    return PolicyDecision(
        decision=decision, action=action, rule=rule, reason="Because.", amount=Decimal("79.99")
    )


def _state(**fields: Any) -> AgentState:
    return AgentState(ticket_id="t", customer_id="cus_001", **fields)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            ActionResult(action=ProposedAction.REFUND, amount=Decimal("79.99")),
            "I've refunded $79.99 to your card ending 4242",
        ),
        (
            ActionResult(action=ProposedAction.REPLACEMENT),
            "A replacement for order ORD-10001 is on its way",
        ),
        (
            ActionResult(
                action=ProposedAction.CANCEL, amount=Decimal("45.00"), payment_action="refunded"
            ),
            "is cancelled, and $45.00 has been refunded to your card ending 4242",
        ),
        (
            ActionResult(action=ProposedAction.CANCEL, payment_action="voided"),
            "the payment hold has been released",
        ),
    ],
)
def test_completed_actions(result: ActionResult, expected: str) -> None:
    state = _state(
        order=_order(),
        decision=_decision(Decision.AUTO_APPROVE, Rule.WITHIN_AUTO_APPROVE_LIMIT),
        result=result,
    )
    assert expected in replies.outcome(state)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (Rule.OWNERSHIP_MISMATCH, "couldn't find that order on your account"),
        (Rule.REMEDY_ALREADY_APPLIED, "already been refunded or replaced"),
        (Rule.OUTSIDE_WINDOW, "was delivered on Sep 1, 2026, which is outside our 30-day window"),
        (Rule.UNKNOWN_ITEM, "couldn't match the item you mentioned"),
        (Rule.ORDER_REQUIRED, "share the order number"),
        (Rule.AMOUNT_MISMATCH, "I'm not able to make that change to order ORD-10001. Because."),
    ],
)
def test_denials_explain_without_leaking(rule: Rule, expected: str) -> None:
    state = _state(order=_order(), decision=_decision(Decision.DENY, rule))
    assert expected in replies.outcome(state)


@pytest.mark.parametrize(
    ("order", "rule", "expected"),
    [
        (
            _order(status=OrderStatus.PROCESSING, delivered_at=None),
            Rule.NOT_DELIVERED,
            "still being prepared",
        ),
        (
            _order(status=OrderStatus.IN_TRANSIT, delivered_at=None, estimated_delivery_at=NOW),
            Rule.NOT_DELIVERED,
            "It's expected by Oct 17, 2026. Tracking number: 1Z999.",
        ),
        (
            _order(status=OrderStatus.CANCELLED, delivered_at=None),
            Rule.NOT_DELIVERED,
            "was cancelled",
        ),
        (_order(), Rule.CANCEL_AFTER_DELIVERY, "so it can't be cancelled"),
        (
            _order(status=OrderStatus.CANCELLED, cancelled_at=NOW),
            Rule.ALREADY_CANCELLED,
            "already cancelled on Oct 17, 2026",
        ),
    ],
)
def test_status_answers(order: Order, rule: Rule, expected: str) -> None:
    decision = _decision(Decision.AUTO_APPROVE, rule, ProposedAction.INFO)
    assert expected in replies.outcome(_state(order=order, decision=decision))


def test_plain_answers_use_the_model_reply_with_its_citations() -> None:
    snippet = PolicySnippet(
        source="billing_policy.md", section="3.2 Refund timing", content="", score=0.9
    )
    proposal = Proposal(
        action=ProposedAction.INFO,
        reason="r",
        confidence=0.9,
        citations=[Citation(source="billing_policy.md", section="3.2 Refund timing")],
        customer_reply="Refunds post in 5 to 10 business days.",
    )
    state = _state(
        decision=_decision(Decision.AUTO_APPROVE, Rule.INFO_ONLY, ProposedAction.INFO),
        proposal=proposal,
        policy_snippets=[snippet],
    )
    assert replies.outcome(state) == (
        "Refunds post in 5 to 10 business days."
        "\n\nPolicy: Billing policy, section 3.2: Refund timing"
    )


def test_missing_answer_falls_back_to_a_safe_message() -> None:
    state = _state(decision=_decision(Decision.AUTO_APPROVE, Rule.INFO_ONLY, ProposedAction.INFO))
    assert "member of our team will follow up" in replies.outcome(state)
    assert "member of our team will follow up" in replies.outcome(_state())


def test_rejected_approval_does_not_reveal_the_reason() -> None:
    state = _state(
        order=_order(),
        decision=_decision(Decision.NEEDS_APPROVAL, Rule.NEEDS_SUPERVISOR),
        approval=ApprovalOutcome(approval_id="a", status=ApprovalStatus.REJECTED),
    )
    assert "wasn't able to approve it" in replies.outcome(state)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            _state(decision=_decision(Decision.ESCALATE, Rule.DELIVERY_OVERDUE)),
            "past its estimated delivery date",
        ),
        (_state(escalation=EscalationCause.CLARIFICATION_LIMIT), "make sure we get this right"),
        (_state(escalation=EscalationCause.TOOL_FAILURE), "passed your request to a specialist"),
    ],
)
def test_escalation_messages(state: AgentState, expected: str) -> None:
    assert expected in replies.escalated(state)


def test_approval_request_names_the_action_and_amount() -> None:
    for action, text in (
        (ProposedAction.REFUND, "a refund of $79.99"),
        (ProposedAction.REPLACEMENT, "a replacement worth $79.99"),
        (ProposedAction.CANCEL, "cancelling the order ($79.99)"),
    ):
        decision = _decision(Decision.NEEDS_APPROVAL, Rule.NEEDS_SUPERVISOR, action)
        assert text in replies.approval_requested(decision, _order())
