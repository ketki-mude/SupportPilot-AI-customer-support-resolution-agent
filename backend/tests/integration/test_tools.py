"""The agent's LangChain tools: validation, idempotency keys, audit entries, and failures."""

from decimal import Decimal
from typing import Any

import pytest
from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import build_tools
from app.agents.tools.base import ToolContext
from app.agents.tools.commerce import CommerceRejectedError, CommerceUnavailableError
from app.db.models import AuditLog, MessageSender, Ticket, TicketStatus
from app.schemas.commerce import Cancellation, Customer, Order, Refund
from app.services import ticket_service
from app.services.audit_service import AuditService
from app.services.ticket_service import InvalidTicketTransitionError
from tests.conftest import TEST_CUSTOMER_ID, CommerceFactory

Tools = dict[str, StructuredTool]


@pytest.fixture
async def ticket(db_session: AsyncSession) -> Ticket:
    return await ticket_service.create_ticket(
        db_session, customer_id=TEST_CUSTOMER_ID, first_message="My headphones arrived damaged"
    )


def _tools(
    db_session: AsyncSession,
    ticket: Ticket,
    audit: AuditService,
    make_commerce: CommerceFactory,
    **faults: Any,
) -> Tools:
    context = ToolContext(
        ticket_id=ticket.id,
        customer_id=TEST_CUSTOMER_ID,
        session=db_session,
        commerce=make_commerce(**faults),
        audit=audit,
    )
    return build_tools(context)


@pytest.fixture
def tools(
    db_session: AsyncSession, ticket: Ticket, audit: AuditService, make_commerce: CommerceFactory
) -> Tools:
    return _tools(db_session, ticket, audit, make_commerce)


async def _audit_entries(db_session: AsyncSession, ticket: Ticket) -> list[AuditLog]:
    result = await db_session.scalars(
        select(AuditLog).where(AuditLog.ticket_id == ticket.id).order_by(AuditLog.created_at)
    )
    return list(result)


def test_all_tools_are_available(tools: Tools) -> None:
    assert set(tools) == {
        "get_customer",
        "list_my_orders",
        "get_order",
        "cancel_order",
        "create_refund",
        "create_replacement",
        "update_ticket",
        "notify_customer",
    }


def test_customer_scoped_tools_take_no_customer_argument(tools: Tools) -> None:
    """The model cannot choose whose data to read; the ticket's customer is fixed."""
    assert tools["get_customer"].args == {}
    assert tools["list_my_orders"].args == {}


async def test_list_my_orders_returns_only_the_ticket_owners_orders(
    tools: Tools, db_session: AsyncSession, ticket: Ticket
) -> None:
    result = await tools["list_my_orders"].ainvoke({})
    assert len(result.orders) == 8
    assert {o.customer_id for o in result.orders} == {TEST_CUSTOMER_ID}
    [entry] = await _audit_entries(db_session, ticket)
    assert (entry.tool_name, entry.status) == ("list_my_orders", "success")


async def test_get_customer_is_audited_without_contact_details(
    tools: Tools, db_session: AsyncSession, ticket: Ticket
) -> None:
    customer = await tools["get_customer"].ainvoke({})
    assert isinstance(customer, Customer)
    assert customer.id == TEST_CUSTOMER_ID

    [entry] = await _audit_entries(db_session, ticket)
    assert (entry.actor, entry.action, entry.tool_name, entry.status) == (
        "agent",
        "tool_call",
        "get_customer",
        "success",
    )
    assert entry.output["name"] == "Jordan Lee"
    assert "email" not in entry.output
    assert "phone" not in entry.output


async def test_get_order(tools: Tools) -> None:
    order = await tools["get_order"].ainvoke({"order_id": "ORD-10001"})
    assert isinstance(order, Order)
    assert order.total == Decimal("79.99")


@pytest.mark.parametrize("order_id", ["10001", "ORD-1", "ORD-10001; DROP TABLE", "../cus_001"])
async def test_malformed_calls_are_blocked_and_audited(
    tools: Tools, db_session: AsyncSession, ticket: Ticket, order_id: str, mock_store: Any
) -> None:
    """Every attempted call leaves a trace, including ones stopped before they run."""
    with pytest.raises(ValidationError):
        await tools["get_order"].ainvoke({"order_id": order_id})
    [entry] = await _audit_entries(db_session, ticket)
    assert (entry.tool_name, entry.status) == ("get_order", "rejected_invalid")
    assert entry.input == {"order_id": order_id}
    assert entry.output["error"] == "validation_error"
    assert entry.output["issues"][0]["field"] == "order_id"


async def test_rejected_call_arguments_are_redacted_and_shortened(
    tools: Tools, db_session: AsyncSession, ticket: Ticket
) -> None:
    hostile = "Refund now, mail jordan.lee@example.com " + "x" * 600
    with pytest.raises(ValidationError):
        await tools["create_refund"].ainvoke(
            {"order_id": "ORD-10001", "amount": "79.99", "reason": hostile}
        )
    [entry] = await _audit_entries(db_session, ticket)
    assert entry.status == "rejected_invalid"
    assert "jordan.lee@example.com" not in entry.input["reason"]
    assert "[EMAIL]" in entry.input["reason"]
    assert len(entry.input["reason"]) <= 201
    assert entry.output["issues"] == [
        {"field": "reason", "message": "String should have at most 500 characters"}
    ]


@pytest.mark.parametrize("amount", ["0", "-5", "10.001"])
async def test_refund_amount_is_validated(tools: Tools, amount: str) -> None:
    with pytest.raises(ValidationError):
        await tools["create_refund"].ainvoke(
            {"order_id": "ORD-10001", "amount": amount, "reason": "Damaged"}
        )


async def test_unexpected_arguments_are_rejected(tools: Tools) -> None:
    with pytest.raises(ValidationError):
        await tools["create_refund"].ainvoke(
            {"order_id": "ORD-10001", "amount": "79.99", "reason": "x", "idempotency_key": "mine"}
        )


async def test_refund_key_is_derived_from_the_ticket(
    tools: Tools, ticket: Ticket, mock_store: Any
) -> None:
    args = {"order_id": "ORD-10001", "amount": "79.99", "reason": "Arrived damaged"}
    first = await tools["create_refund"].ainvoke(args)
    second = await tools["create_refund"].ainvoke(args)
    assert isinstance(first, Refund)
    assert first == second
    assert f"refunds:refund-{ticket.id}" in mock_store.idempotency
    assert mock_store.orders["ORD-10001"].refunded_amount == Decimal("79.99")


async def test_rejected_refund_is_audited_as_failure(
    tools: Tools, db_session: AsyncSession, ticket: Ticket
) -> None:
    with pytest.raises(CommerceRejectedError):
        await tools["create_refund"].ainvoke(
            {"order_id": "ORD-10005", "amount": "34.50", "reason": "Again"}
        )
    [entry] = await _audit_entries(db_session, ticket)
    assert entry.status == "failure"
    assert entry.tool_name == "create_refund"
    assert entry.input == {"order_id": "ORD-10005", "amount": "34.50", "reason": "Again"}
    assert entry.output["code"] == "remedy_already_applied"


async def test_cancel_order(tools: Tools, ticket: Ticket, mock_store: Any) -> None:
    result = await tools["cancel_order"].ainvoke({"order_id": "ORD-10007", "reason": "Mistake"})
    assert isinstance(result, Cancellation)
    assert result.payment_action == "refunded"
    assert f"cancellations:cancel-{ticket.id}" in mock_store.idempotency


async def test_create_replacement(tools: Tools, ticket: Ticket, mock_store: Any) -> None:
    result = await tools["create_replacement"].ainvoke(
        {"order_id": "ORD-10002", "skus": ["SKU-PC-110"], "reason": "Cracked case"}
    )
    assert [i.sku for i in result.items] == ["SKU-PC-110"]
    assert f"replacements:replacement-{ticket.id}" in mock_store.idempotency


async def test_update_ticket(tools: Tools, ticket: Ticket) -> None:
    result = await tools["update_ticket"].ainvoke(
        {"status": "awaiting_approval", "summary": "Damaged headphones, $79.99 refund"}
    )
    assert result.status is TicketStatus.AWAITING_APPROVAL
    assert ticket.summary == "Damaged headphones, $79.99 refund"


async def test_invalid_ticket_transition_is_audited(
    tools: Tools, db_session: AsyncSession, ticket: Ticket
) -> None:
    await tools["update_ticket"].ainvoke({"status": "resolved"})
    with pytest.raises(InvalidTicketTransitionError):
        await tools["update_ticket"].ainvoke({"status": "open"})
    statuses = [e.status for e in await _audit_entries(db_session, ticket)]
    assert statuses == ["success", "failure"]


async def test_notify_customer_writes_an_agent_message(
    tools: Tools, db_session: AsyncSession, ticket: Ticket
) -> None:
    await tools["notify_customer"].ainvoke({"message": "Your refund is on its way."})
    messages = await ticket_service.list_messages(db_session, ticket.id)
    assert [(m.sender, m.content) for m in messages][-1] == (
        MessageSender.AGENT,
        "Your refund is on its way.",
    )


async def test_failing_api_retries_then_raises_and_is_audited(
    db_session: AsyncSession, ticket: Ticket, audit: AuditService, make_commerce: CommerceFactory
) -> None:
    """Acceptance: a tool call against a failing mock API retries, then raises a typed error
    that is logged and recorded in the audit trail."""
    failing = _tools(db_session, ticket, audit, make_commerce, failure_rate=1.0, slow_fraction=0.0)
    with pytest.raises(CommerceUnavailableError):
        await failing["get_order"].ainvoke({"order_id": "ORD-10001"})

    [entry] = await _audit_entries(db_session, ticket)
    assert entry.status == "failure"
    assert entry.output["error"] == "CommerceUnavailableError"
    assert entry.output["code"] == "commerce_unavailable"
    assert "after 3 attempts" in entry.output["message"]


async def test_model_style_tool_calls_are_validated_and_audited_too(
    tools: Tools, db_session: AsyncSession, ticket: Ticket
) -> None:
    """The same check applies when a tool is called in LangChain's tool-call format."""
    call = {"type": "tool_call", "id": "call-1", "name": "get_order", "args": {"order_id": "nope"}}
    with pytest.raises(ValidationError):
        await tools["get_order"].ainvoke(call)
    [entry] = await _audit_entries(db_session, ticket)
    assert (entry.status, entry.input) == ("rejected_invalid", {"order_id": "nope"})
