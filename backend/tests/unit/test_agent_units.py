"""Order selection, the event bus, citations, and customer reply templates."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.agents import replies
from app.agents.nodes.intake import mentioned_order_ids, select_order
from app.agents.nodes.resolution import grounded_citations
from app.agents.runtime import EventBus, EventType
from app.agents.state import ChatTurn, Clarification, OrderCandidate
from app.db.models import MessageSender, TicketCategory
from app.schemas.agent import Citation, PolicySnippet
from app.schemas.commerce import CustomerOrders, Order, OrderItem, OrderStatus, PaymentStatus

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _order(order_id: str, name: str = "Thing", customer: str = "cus_001") -> Order:
    return Order(
        order_id=order_id,
        customer_id=customer,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.CAPTURED,
        items=[OrderItem(sku="SKU-1", name=name, quantity=1, unit_price=Decimal("10.00"))],
        total=Decimal("10.00"),
        currency="USD",
        placed_at=NOW - timedelta(days=9),
        shipped_at=NOW - timedelta(days=7),
        delivered_at=NOW - timedelta(days=4),
        estimated_delivery_at=None,
        cancelled_at=None,
        tracking_number=None,
        payment_method_last4="4242",
        refunded_amount=Decimal("0.00"),
        remedy=None,
    )


OWN = CustomerOrders(orders=[_order("ORD-10001", "Headphones"), _order("ORD-10002", "Case")])
REFUND = TicketCategory.REFUND


def _turn(text: str, sender: MessageSender = MessageSender.CUSTOMER) -> ChatTurn:
    return ChatTurn(sender=sender, content=text)


def test_mentioned_ids_come_from_the_latest_customer_message_that_has_any() -> None:
    turns = [
        _turn("refund ORD-10001"),
        _turn("Which order? ORD-55555?", MessageSender.AGENT),
        _turn("sorry, it was ord-10002"),
        _turn("thanks"),
    ]
    assert mentioned_order_ids(turns) == ["ORD-10002"]


def test_a_typed_order_number_is_used_even_if_not_owned() -> None:
    assert select_order(REFUND, ["ORD-10009"], [], OWN).order_id == "ORD-10009"


def test_model_matches_must_be_the_customers_own() -> None:
    assert select_order(REFUND, [], ["ORD-10009", "ORD-10001"], OWN).order_id == "ORD-10001"
    selection = select_order(REFUND, [], ["ORD-10009"], OWN)
    assert (selection.order_id, selection.clarification) == (None, Clarification.UNMATCHED)


def test_several_matches_ask_with_candidates() -> None:
    selection = select_order(REFUND, [], ["ORD-10001", "ORD-10002", "ORD-10001"], OWN)
    assert selection.clarification is Clarification.AMBIGUOUS
    assert [c.order_id for c in selection.candidates] == ["ORD-10001", "ORD-10002"]


def test_questions_without_an_order_do_not_need_one() -> None:
    selection = select_order(TicketCategory.BILLING, [], [], OWN)
    assert (selection.order_id, selection.clarification) == (None, None)


def test_two_typed_order_numbers_ask_which_one() -> None:
    selection = select_order(REFUND, ["ORD-10001", "ORD-10002"], [], OWN)
    assert selection.clarification is Clarification.AMBIGUOUS


def test_clarification_describes_orders_by_item_and_date() -> None:
    candidates = [OrderCandidate.from_order(o) for o in OWN.orders]
    text = replies.clarification(Clarification.AMBIGUOUS, candidates)
    assert "- ORD-10001: Headphones, delivered Oct 12, 2026" in text
    assert "- ORD-10002: Case, delivered Oct 12, 2026" in text
    assert "order number" in replies.clarification(Clarification.UNMATCHED, [])


def test_citations_are_limited_to_retrieved_sections() -> None:
    snippets = [
        PolicySnippet(
            source="refund_policy.md",
            section="3.2 Damaged or defective items",
            content="",
            score=0.8,
        ),
        PolicySnippet(
            source="shipping_policy.md", section="3.1 Late deliveries", content="", score=0.7
        ),
    ]
    cited = [
        Citation(source="refund_policy.md", section="3.2"),
        Citation(source="refund_policy.md", section="3.2 Damaged or defective items"),
        Citation(source="refund_policy.md", section="9.9"),
        Citation(source="made_up.md", section="1.1"),
    ]
    assert grounded_citations(cited, snippets) == [
        Citation(source="refund_policy.md", section="3.2 Damaged or defective items")
    ]


async def test_event_bus_replays_history_then_streams_live() -> None:
    bus = EventBus()
    bus.publish("t1", EventType.STEP_STARTED, {"step": "intake"})
    bus.publish("t2", EventType.STEP_STARTED, {"step": "other ticket"})
    stream = bus.subscribe("t1", heartbeat=0.05)

    first = await anext(stream)
    assert first is not None and first.data == {"step": "intake"}
    bus.publish("t1", EventType.COMPLETED, {"ticket_status": "resolved"})
    second = await anext(stream)
    assert second is not None and second.type is EventType.COMPLETED
    assert await anext(stream) is None  # heartbeat when nothing happens
    await stream.aclose()


async def test_event_bus_resumes_after_last_event_id() -> None:
    bus = EventBus()
    for step in ("a", "b", "c"):
        bus.publish("t1", EventType.STEP_STARTED, {"step": step})
    stream = bus.subscribe("t1", after=2, heartbeat=0.05)
    event = await anext(stream)
    assert event is not None and (event.id, event.data["step"]) == (3, "c")
    await stream.aclose()


def test_event_encoding_is_valid_sse() -> None:
    bus = EventBus()
    event = bus.publish("t1", EventType.APPROVAL_REQUESTED, {"amount": Decimal("79.99")})
    assert event.encode() == 'id: 1\nevent: approval_requested\ndata: {"amount":"79.99"}\n\n'
