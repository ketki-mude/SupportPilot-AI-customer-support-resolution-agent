"""Every business rule, the order they apply in, and the boundaries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.core.config import Settings, get_settings
from app.db.models import TicketCategory
from app.schemas.agent import Decision, PolicyDecision, Proposal, ProposedAction, Rule
from app.schemas.commerce import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    Remedy,
)
from app.services.policy_engine import PolicyContext, days_since, evaluate

NOW = datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
REQUESTER = "cus_001"

REFUND = ProposedAction.REFUND
REPLACEMENT = ProposedAction.REPLACEMENT
CANCEL = ProposedAction.CANCEL
REMEDIES = pytest.mark.parametrize("action", [REFUND, REPLACEMENT])


@pytest.fixture
def settings() -> Settings:
    """Spec defaults, independent of whatever the environment sets."""
    return get_settings().model_copy(
        update={
            "refund_window_days": 30,
            "auto_approve_max_usd": Decimal(50),
            "supervisor_approval_max_usd": Decimal(500),
            "min_confidence": 0.6,
            "cancel_use_refund_limits": False,
        }
    )


def customer(*, fraud_flag: bool = False) -> Customer:
    return Customer(
        id=REQUESTER,
        name="Jordan Lee",
        email="jordan@example.com",
        phone="+1-555-0101",
        tier="gold",
        fraud_flag=fraud_flag,
        member_since=NOW - timedelta(days=800),
    )


def item(price: str, sku: str = "SKU-A", quantity: int = 1) -> OrderItem:
    return OrderItem(sku=sku, name=f"Item {sku}", quantity=quantity, unit_price=Decimal(price))


def order(*, items: list[OrderItem] | None = None, **overrides: Any) -> Order:
    """A delivered, captured, unremedied order owned by the requester, 5 days after delivery."""
    lines = items or [item("79.99")]
    values: dict[str, Any] = {
        "order_id": "ORD-10001",
        "customer_id": REQUESTER,
        "status": OrderStatus.DELIVERED,
        "payment_status": PaymentStatus.CAPTURED,
        "items": lines,
        "total": sum((line.line_total for line in lines), Decimal("0.00")),
        "currency": "USD",
        "placed_at": NOW - timedelta(days=9),
        "shipped_at": NOW - timedelta(days=7),
        "delivered_at": NOW - timedelta(days=5),
        "estimated_delivery_at": None,
        "cancelled_at": None,
        "tracking_number": "1Z999",
        "payment_method_last4": "4242",
        "refunded_amount": Decimal("0.00"),
        "remedy": None,
    }
    return Order(**(values | overrides))


def in_flight(status: OrderStatus, *, eta_in_days: int) -> Order:
    return order(
        status=status,
        delivered_at=None,
        estimated_delivery_at=NOW + timedelta(days=eta_in_days),
    )


def proposal(action: ProposedAction = REFUND, **overrides: Any) -> Proposal:
    values: dict[str, Any] = {"action": action, "reason": "Arrived damaged", "confidence": 0.9}
    return Proposal(**(values | overrides))


def decide(
    settings: Settings,
    prop: Proposal,
    current: Order | None,
    *,
    intent: TicketCategory = TicketCategory.REFUND,
    requester: Customer | None = None,
    now: datetime = NOW,
) -> PolicyDecision:
    context = PolicyContext(
        requester_customer_id=REQUESTER,
        intent=intent,
        customer=requester or customer(),
        order=current,
    )
    return evaluate(prop, context, settings, now=now)


def assert_decision(result: PolicyDecision, decision: Decision, rule: Rule) -> None:
    assert (result.decision, result.rule) == (decision, rule), result.reason


# --- Checks that apply to every action, in order -------------------------------------------


@pytest.mark.parametrize("action", list(ProposedAction))
def test_wrong_owner_is_denied_for_every_action(settings: Settings, action: ProposedAction) -> None:
    result = decide(settings, proposal(action), order(customer_id="cus_009"))
    assert_decision(result, Decision.DENY, Rule.OWNERSHIP_MISMATCH)


def test_ownership_is_checked_before_everything_else(settings: Settings) -> None:
    result = decide(
        settings,
        proposal(confidence=0.1),
        order(customer_id="cus_009"),
        intent=TicketCategory.OTHER,
        requester=customer(fraud_flag=True),
    )
    assert result.rule is Rule.OWNERSHIP_MISMATCH


@pytest.mark.parametrize("action", [REFUND, REPLACEMENT, CANCEL, ProposedAction.INFO])
def test_fraud_flag_escalates(settings: Settings, action: ProposedAction) -> None:
    current = (
        order(status=OrderStatus.PROCESSING, delivered_at=None) if action is CANCEL else order()
    )
    result = decide(settings, proposal(action), current, requester=customer(fraud_flag=True))
    assert_decision(result, Decision.ESCALATE, Rule.FRAUD_FLAG)


def test_fraud_flag_escalates_even_a_tiny_refund(settings: Settings) -> None:
    result = decide(
        settings, proposal(), order(items=[item("1.00")]), requester=customer(fraud_flag=True)
    )
    assert result.decision is Decision.ESCALATE


def test_unknown_intent_escalates(settings: Settings) -> None:
    result = decide(settings, proposal(), order(), intent=TicketCategory.OTHER)
    assert_decision(result, Decision.ESCALATE, Rule.UNKNOWN_INTENT)


def test_confidence_below_threshold_escalates(settings: Settings) -> None:
    result = decide(settings, proposal(confidence=0.59), order())
    assert_decision(result, Decision.ESCALATE, Rule.LOW_CONFIDENCE)


def test_confidence_at_threshold_is_accepted(settings: Settings) -> None:
    result = decide(settings, proposal(confidence=0.6), order())
    assert result.rule is not Rule.LOW_CONFIDENCE


def test_agent_can_ask_for_a_human(settings: Settings) -> None:
    result = decide(settings, proposal(ProposedAction.ESCALATE), order())
    assert_decision(result, Decision.ESCALATE, Rule.AGENT_REQUESTED_ESCALATION)


def test_information_requests_are_auto_approved(settings: Settings) -> None:
    result = decide(settings, proposal(ProposedAction.INFO), order(), intent=TicketCategory.BILLING)
    assert_decision(result, Decision.AUTO_APPROVE, Rule.INFO_ONLY)
    assert result.action is ProposedAction.INFO
    assert result.amount is None


@pytest.mark.parametrize("action", [REFUND, REPLACEMENT, CANCEL])
def test_actions_need_an_order(settings: Settings, action: ProposedAction) -> None:
    result = decide(settings, proposal(action), None)
    assert_decision(result, Decision.DENY, Rule.ORDER_REQUIRED)


# --- Refunds and replacements ---------------------------------------------------------------


@REMEDIES
@pytest.mark.parametrize(
    "current",
    [
        order(status=OrderStatus.PROCESSING, delivered_at=None, shipped_at=None),
        in_flight(OrderStatus.SHIPPED, eta_in_days=3),
        in_flight(OrderStatus.IN_TRANSIT, eta_in_days=1),
        order(status=OrderStatus.CANCELLED, delivered_at=None, shipped_at=None),
    ],
    ids=["processing", "shipped", "in_transit", "cancelled"],
)
def test_undelivered_orders_only_get_status(
    settings: Settings, action: ProposedAction, current: Order
) -> None:
    result = decide(settings, proposal(action), current)
    assert_decision(result, Decision.AUTO_APPROVE, Rule.NOT_DELIVERED)
    assert result.action is ProposedAction.INFO
    assert result.amount is None


@REMEDIES
@pytest.mark.parametrize("status", [OrderStatus.SHIPPED, OrderStatus.IN_TRANSIT])
def test_overdue_delivery_escalates(
    settings: Settings, action: ProposedAction, status: OrderStatus
) -> None:
    result = decide(settings, proposal(action), in_flight(status, eta_in_days=-4))
    assert_decision(result, Decision.ESCALATE, Rule.DELIVERY_OVERDUE)


@REMEDIES
@pytest.mark.parametrize(
    "overrides",
    [
        {"remedy": Remedy.REFUND, "refunded_amount": Decimal("79.99")},
        {"remedy": Remedy.REPLACEMENT},
        {"refunded_amount": Decimal("10.00")},
    ],
    ids=["refunded", "replaced", "partly_refunded"],
)
def test_one_remedy_per_order(
    settings: Settings, action: ProposedAction, overrides: dict[str, Any]
) -> None:
    result = decide(settings, proposal(action), order(**overrides))
    assert_decision(result, Decision.DENY, Rule.REMEDY_ALREADY_APPLIED)


def test_delivered_without_a_date_escalates(settings: Settings) -> None:
    result = decide(settings, proposal(), order(delivered_at=None))
    assert_decision(result, Decision.ESCALATE, Rule.DELIVERY_DATE_MISSING)


@REMEDIES
@pytest.mark.parametrize(("days", "allowed"), [(0, True), (29, True), (30, True), (31, False)])
def test_refund_window_boundaries(
    settings: Settings, action: ProposedAction, days: int, allowed: bool
) -> None:
    result = decide(settings, proposal(action), order(delivered_at=NOW - timedelta(days=days)))
    if allowed:
        assert result.decision is Decision.NEEDS_APPROVAL  # 79.99 sits in the supervisor band
    else:
        assert_decision(result, Decision.DENY, Rule.OUTSIDE_WINDOW)


def test_window_counts_calendar_days_not_hours(settings: Settings) -> None:
    delivered_late = datetime(2026, 2, 13, 23, 59, tzinfo=UTC)
    # 30 days and 2 minutes later, but already calendar day 31: denied.
    result = decide(
        settings,
        proposal(),
        order(delivered_at=delivered_late),
        now=datetime(2026, 3, 16, 0, 1, tzinfo=UTC),
    )
    assert result.rule is Rule.OUTSIDE_WINDOW
    # 30 days and 23 hours later, still calendar day 30: allowed.
    delivered_early = datetime(2026, 2, 13, 0, 1, tzinfo=UTC)
    result = decide(
        settings,
        proposal(),
        order(delivered_at=delivered_early),
        now=datetime(2026, 3, 15, 23, 59, tzinfo=UTC),
    )
    assert result.rule is not Rule.OUTSIDE_WINDOW


def test_days_since_uses_utc_dates() -> None:
    assert (
        days_since(datetime(2026, 1, 1, 23, 0, tzinfo=UTC), datetime(2026, 1, 2, 1, 0, tzinfo=UTC))
        == 1
    )
    assert (
        days_since(datetime(2026, 1, 1, 1, 0, tzinfo=UTC), datetime(2026, 1, 1, 23, 0, tzinfo=UTC))
        == 0
    )


@REMEDIES
@pytest.mark.parametrize(
    ("price", "decision", "rule"),
    [
        ("0.01", Decision.AUTO_APPROVE, Rule.WITHIN_AUTO_APPROVE_LIMIT),
        ("50.00", Decision.AUTO_APPROVE, Rule.WITHIN_AUTO_APPROVE_LIMIT),
        ("50.01", Decision.NEEDS_APPROVAL, Rule.NEEDS_SUPERVISOR),
        ("79.99", Decision.NEEDS_APPROVAL, Rule.NEEDS_SUPERVISOR),
        ("500.00", Decision.NEEDS_APPROVAL, Rule.NEEDS_SUPERVISOR),
        ("500.01", Decision.ESCALATE, Rule.OVER_SUPERVISOR_LIMIT),
        ("1299.00", Decision.ESCALATE, Rule.OVER_SUPERVISOR_LIMIT),
    ],
)
def test_amount_bands(
    settings: Settings, action: ProposedAction, price: str, decision: Decision, rule: Rule
) -> None:
    result = decide(settings, proposal(action), order(items=[item(price)]))
    assert_decision(result, decision, rule)
    assert result.action is action
    assert result.amount == Decimal(price)


def test_amount_comes_from_the_order_not_the_proposal(settings: Settings) -> None:
    result = decide(settings, proposal(), order(items=[item("29.98")]))
    assert result.amount == Decimal("29.98")
    assert result.decision is Decision.AUTO_APPROVE


def test_inflated_amount_is_denied(settings: Settings) -> None:
    result = decide(settings, proposal(amount=Decimal("500.00")), order(items=[item("29.98")]))
    assert_decision(result, Decision.DENY, Rule.AMOUNT_MISMATCH)


def test_understated_amount_is_denied(settings: Settings) -> None:
    # Claiming 40.00 for a 79.99 item would sneak a supervisor-band refund under the auto limit.
    result = decide(settings, proposal(amount=Decimal("40.00")), order(items=[item("79.99")]))
    assert_decision(result, Decision.DENY, Rule.AMOUNT_MISMATCH)


def test_matching_amount_is_accepted(settings: Settings) -> None:
    result = decide(settings, proposal(amount=Decimal("79.99")), order())
    assert result.decision is Decision.NEEDS_APPROVAL


def test_selected_items_set_the_amount(settings: Settings) -> None:
    lines = [item("19.99", "SKU-CASE"), item("9.99", "SKU-GLASS"), item("12.99", "SKU-CABLE", 3)]
    result = decide(settings, proposal(skus=["SKU-CABLE", "SKU-GLASS"]), order(items=lines))
    assert result.amount == Decimal("48.96")
    assert result.skus == ["SKU-GLASS", "SKU-CABLE"]
    assert result.decision is Decision.AUTO_APPROVE


def test_whole_order_is_used_when_no_items_are_named(settings: Settings) -> None:
    lines = [item("19.99", "SKU-CASE"), item("9.99", "SKU-GLASS")]
    result = decide(settings, proposal(REPLACEMENT), order(items=lines))
    assert result.amount == Decimal("29.98")
    assert result.skus == ["SKU-CASE", "SKU-GLASS"]


def test_unknown_items_are_denied(settings: Settings) -> None:
    result = decide(settings, proposal(skus=["SKU-NOT-ON-ORDER"]), order())
    assert_decision(result, Decision.DENY, Rule.UNKNOWN_ITEM)


def test_thresholds_come_from_settings(settings: Settings) -> None:
    stricter = settings.model_copy(
        update={"auto_approve_max_usd": Decimal(25), "refund_window_days": 14}
    )
    assert (
        decide(stricter, proposal(), order(items=[item("29.98")])).decision
        is Decision.NEEDS_APPROVAL
    )
    old = order(items=[item("10.00")], delivered_at=NOW - timedelta(days=15))
    assert decide(stricter, proposal(), old).rule is Rule.OUTSIDE_WINDOW


# --- Cancellations --------------------------------------------------------------------------


def processing(price: str = "45.00", payment: PaymentStatus = PaymentStatus.CAPTURED) -> Order:
    return order(
        items=[item(price)],
        status=OrderStatus.PROCESSING,
        payment_status=payment,
        shipped_at=None,
        delivered_at=None,
    )


@pytest.mark.parametrize("price", ["45.00", "329.99", "1299.00"])
@pytest.mark.parametrize("payment", [PaymentStatus.CAPTURED, PaymentStatus.AUTHORIZED])
def test_unshipped_cancellation_is_auto_approved_at_any_amount(
    settings: Settings, price: str, payment: PaymentStatus
) -> None:
    result = decide(
        settings, proposal(CANCEL), processing(price, payment), intent=TicketCategory.CANCELLATION
    )
    assert_decision(result, Decision.AUTO_APPROVE, Rule.CANCEL_UNSHIPPED)
    assert result.action is CANCEL
    assert result.amount == Decimal(price)
    assert ("refunded" if payment is PaymentStatus.CAPTURED else "released") in result.reason


@pytest.mark.parametrize(
    ("price", "decision"),
    [
        ("45.00", Decision.AUTO_APPROVE),
        ("50.01", Decision.NEEDS_APPROVAL),
        ("500.00", Decision.NEEDS_APPROVAL),
        ("500.01", Decision.ESCALATE),
    ],
)
def test_cancel_limits_flag_applies_refund_bands(
    settings: Settings, price: str, decision: Decision
) -> None:
    strict = settings.model_copy(update={"cancel_use_refund_limits": True})
    result = decide(strict, proposal(CANCEL), processing(price))
    assert result.decision is decision
    assert result.action is CANCEL


@pytest.mark.parametrize("status", [OrderStatus.SHIPPED, OrderStatus.IN_TRANSIT])
def test_cancelling_a_shipped_order_escalates(settings: Settings, status: OrderStatus) -> None:
    result = decide(settings, proposal(CANCEL), in_flight(status, eta_in_days=2))
    assert_decision(result, Decision.ESCALATE, Rule.CANCEL_AFTER_SHIPMENT)


def test_delivered_order_cannot_be_cancelled_but_refund_is_offered(settings: Settings) -> None:
    result = decide(settings, proposal(CANCEL), order())
    assert_decision(result, Decision.AUTO_APPROVE, Rule.CANCEL_AFTER_DELIVERY)
    assert result.action is ProposedAction.INFO
    assert "refund" in result.reason
    assert "30 days" in result.reason


def test_already_cancelled_order_only_gets_status(settings: Settings) -> None:
    current = order(
        status=OrderStatus.CANCELLED, payment_status=PaymentStatus.VOIDED, delivered_at=None
    )
    result = decide(settings, proposal(CANCEL), current)
    assert_decision(result, Decision.AUTO_APPROVE, Rule.ALREADY_CANCELLED)
    assert result.action is ProposedAction.INFO


def test_cancel_with_wrong_amount_is_denied(settings: Settings) -> None:
    result = decide(settings, proposal(CANCEL, amount=Decimal("1.00")), processing("45.00"))
    assert_decision(result, Decision.DENY, Rule.AMOUNT_MISMATCH)


def test_engine_is_deterministic(settings: Settings) -> None:
    results = {decide(settings, proposal(), order()).model_dump_json() for _ in range(20)}
    assert len(results) == 1
