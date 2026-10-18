"""Staff taking over a ticket: replies, actions checked against the rules, overrides, closing."""

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import replies
from app.agents.runner import AgentRunner
from app.agents.runtime import EventType
from app.db.models import AuditLog, Ticket, TicketStatus
from app.services import ticket_service
from tests.agent_support import Api

Sessions = async_sessionmaker[AsyncSession]


async def handed_over(
    sessions: Sessions,
    order_id: str | None = "ORD-10006",
    status: TicketStatus = TicketStatus.ESCALATED,
    *,
    customer_replied: bool = False,
) -> uuid.UUID:
    """A ticket the agent handed to a specialist, for the demo customer."""
    async with sessions() as session:
        ticket = await ticket_service.create_ticket(
            session, customer_id="cus_001", first_message="Please help", order_id=order_id
        )
        ticket.status = status
        ticket.customer_replied = customer_replied
        await session.commit()
        return ticket.id


async def user_id(api: Api, who: str) -> str:
    return str((await api.get("/auth/me", who)).json()["id"])


async def entries(sessions: Sessions, ticket_id: uuid.UUID, action: str) -> list[AuditLog]:
    async with sessions() as session:
        rows = await session.scalars(
            select(AuditLog)
            .where(AuditLog.ticket_id == ticket_id, AuditLog.action == action)
            .order_by(AuditLog.created_at)
        )
        return list(rows)


async def load(sessions: Sessions, ticket_id: uuid.UUID) -> Ticket:
    async with sessions() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket is not None
        return ticket


def refunds(store: Any, order_id: str) -> list[Any]:
    return [r for r in store.refunds.values() if r.order_id == order_id]


async def test_staff_refund_within_the_rules_resolves_and_tells_the_customer(
    api: Api, live_sessions: Sessions, mock_store: Any, agent: AgentRunner
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10002")
    response = await api.post(
        f"/tickets/{ticket_id}/actions", "agent", {"action": "refund", "skus": ["SKU-SP-020"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["check"]["verdict"] == "allowed"
    assert body["amount"] == "9.99"
    assert body["ticket"]["status"] == "resolved"
    assert body["message"]["sender"] == "staff"
    assert "I've refunded $9.99 to your card ending 4242" in body["message"]["content"]
    assert [r.amount for r in refunds(mock_store, "ORD-10002")] == [Decimal("9.99")]

    staff = await user_id(api, "agent")
    [action] = await entries(live_sessions, ticket_id, "staff_action")
    assert action.actor == staff
    assert action.input["verdict"] == "allowed"
    [tool] = await entries(live_sessions, ticket_id, "tool_call")
    assert (tool.actor, tool.tool_name) == (staff, "create_refund")
    events = [e.type for e in agent.events.history(str(ticket_id))]
    assert events == [EventType.MESSAGE, EventType.COMPLETED]


async def test_overriding_the_rules_needs_a_written_reason(
    api: Api, live_sessions: Sessions, mock_store: Any
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10006")  # the $1,299 laptop
    check = await api.post(f"/tickets/{ticket_id}/actions/check", "agent", {"action": "refund"})
    assert check.json() == {
        "action": "refund",
        "order_id": "ORD-10006",
        "verdict": "override",
        "rule": "over_supervisor_limit",
        "reason": "$1299.00 is above the supervisor limit.",
        "amount": "1299.00",
        "skus": ["SKU-LT-140"],
    }

    for note in (None, "ok"):
        refused = await api.post(
            f"/tickets/{ticket_id}/actions", "agent", {"action": "refund", "note": note}
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "override_reason_required"
    assert refunds(mock_store, "ORD-10006") == []

    reason = "Screen defect confirmed from the customer's photos"
    done = await api.post(
        f"/tickets/{ticket_id}/actions", "supervisor", {"action": "refund", "note": reason}
    )
    assert done.status_code == 200, done.text
    assert [r.amount for r in refunds(mock_store, "ORD-10006")] == [Decimal("1299.00")]
    [action] = await entries(live_sessions, ticket_id, "staff_action")
    assert action.input["verdict"] == "override"
    assert action.input["rule"] == "over_supervisor_limit"
    assert action.input["note"] == reason


async def test_a_refund_outside_the_window_can_be_overridden(
    api: Api, live_sessions: Sessions, mock_store: Any
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10004")  # delivered 45 days ago
    response = await api.post(
        f"/tickets/{ticket_id}/actions",
        "admin",
        {"action": "refund", "note": "Goodwill: the speaker failed just after the window"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["check"]["rule"] == "outside_window"
    assert len(refunds(mock_store, "ORD-10004")) == 1


@pytest.mark.parametrize(
    ("order_id", "body", "rule"),
    [
        ("ORD-10005", {"action": "refund"}, "remedy_already_applied"),
        ("ORD-10001", {"action": "refund", "order_id": "ORD-10009"}, "ownership_mismatch"),
        ("ORD-10001", {"action": "cancel"}, "cancel_after_delivery"),
        ("ORD-10003", {"action": "replacement"}, "not_delivered"),
        ("ORD-10001", {"action": "refund", "skus": ["SKU-NOT-ON-ORDER"]}, "unknown_item"),
        (None, {"action": "refund"}, "order_required"),
    ],
)
async def test_some_actions_are_never_allowed(
    api: Api,
    live_sessions: Sessions,
    mock_store: Any,
    order_id: str | None,
    body: dict[str, Any],
    rule: str,
) -> None:
    ticket_id = await handed_over(live_sessions, order_id)
    check = await api.post(f"/tickets/{ticket_id}/actions/check", "admin", body)
    assert (check.json()["verdict"], check.json()["rule"]) == ("blocked", rule)

    with_reason = body | {"note": "I am sure about this, please do it"}
    refused = await api.post(f"/tickets/{ticket_id}/actions", "admin", with_reason)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "action_not_allowed"
    assert (await load(live_sessions, ticket_id)).status is TicketStatus.ESCALATED
    assert refunds(mock_store, "ORD-10009") == []


async def test_two_staff_acting_at_once_refund_only_once(
    api: Api, live_sessions: Sessions, mock_store: Any
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10008")
    body = {"action": "refund"}
    first, second = await asyncio.gather(
        api.post(f"/tickets/{ticket_id}/actions", "agent", body),
        api.post(f"/tickets/{ticket_id}/actions", "supervisor", body),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["error"]["code"] == "ticket_not_actionable"
    assert len(refunds(mock_store, "ORD-10008")) == 1


async def test_staff_can_cancel_an_unshipped_order(
    api: Api, live_sessions: Sessions, mock_store: Any
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10007")
    response = await api.post(f"/tickets/{ticket_id}/actions", "agent", {"action": "cancel"})
    assert response.status_code == 200, response.text
    assert mock_store.orders["ORD-10007"].status == "cancelled"
    assert "Order ORD-10007 is cancelled" in response.json()["message"]["content"]


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (TicketStatus.AWAITING_APPROVAL, "waiting for an approval decision"),
        (TicketStatus.RESOLVED, "already closed"),
        (TicketStatus.REJECTED, "already closed"),
    ],
)
async def test_only_handed_over_tickets_take_actions(
    api: Api, live_sessions: Sessions, status: TicketStatus, message: str
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10008", status)
    for path, body in (("actions", {"action": "refund"}), ("close", {"outcome": "resolved"})):
        response = await api.post(f"/tickets/{ticket_id}/{path}", "admin", body)
        assert response.status_code == 409
        assert message in response.json()["error"]["message"]


async def test_customers_cannot_use_staff_actions(api: Api, live_sessions: Sessions) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10008")
    for path, body in (
        ("reply", {"message": "hi"}),
        ("actions/check", {"action": "refund"}),
        ("actions", {"action": "refund"}),
        ("close", {"outcome": "resolved"}),
    ):
        assert (await api.post(f"/tickets/{ticket_id}/{path}", "customer", body)).status_code == 403


async def test_a_staff_reply_reaches_the_customer_live(
    api: Api, live_sessions: Sessions, agent: AgentRunner
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10006", customer_replied=True)
    response = await api.post(
        f"/tickets/{ticket_id}/reply", "agent", {"message": "  Could you send a photo?  "}
    )
    assert response.status_code == 201
    assert response.json()["sender"] == "staff"
    assert response.json()["content"] == "Could you send a photo?"

    ticket = await load(live_sessions, ticket_id)
    assert ticket.status is TicketStatus.ESCALATED  # a reply does not change the status
    assert not ticket.customer_replied
    [event] = agent.events.history(str(ticket_id))
    assert (event.type, event.data["sender"]) == (EventType.MESSAGE, "staff")
    seen = (await api.get(f"/tickets/{ticket_id}", "customer")).json()["messages"]
    assert seen[-1]["content"] == "Could you send a photo?"
    [entry] = await entries(live_sessions, ticket_id, "staff_reply")
    assert entry.actor == await user_id(api, "agent")


async def test_blank_replies_are_rejected(api: Api, live_sessions: Sessions) -> None:
    ticket_id = await handed_over(live_sessions)
    response = await api.post(f"/tickets/{ticket_id}/reply", "agent", {"message": "   "})
    assert response.status_code == 422


async def test_closing_without_an_action(
    api: Api, live_sessions: Sessions, mock_store: Any
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10006")
    response = await api.post(
        f"/tickets/{ticket_id}/close", "agent", {"outcome": "declined", "note": "Not a defect"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    messages = (await api.get(f"/tickets/{ticket_id}", "customer")).json()["messages"]
    assert messages[-1] == messages[-1] | {
        "sender": "staff",
        "content": replies.STAFF_CLOSED_DECLINED,
    }
    [entry] = await entries(live_sessions, ticket_id, "staff_close")
    assert entry.input == {"outcome": "declined", "note": "Not a defect"}
    assert refunds(mock_store, "ORD-10006") == []


async def test_a_customer_writing_on_an_escalated_ticket_hears_the_specialist_has_it(
    api: Api, live_sessions: Sessions
) -> None:
    ticket_id = await handed_over(live_sessions, "ORD-10006")
    response = await api.post(
        f"/chat/tickets/{ticket_id}/messages", "customer", {"message": "Any news?"}
    )
    assert [m["content"] for m in response.json()["messages"]] == [
        "Any news?",
        replies.ESCALATED_FOLLOW_UP,
    ]
