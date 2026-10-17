"""The chat, tickets, and approvals API around the agent, including the live event stream."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_runner
from app.db.models import AgentRun
from tests.agent_support import Api, ScriptedLLM, count, intake, resolution

Sessions = async_sessionmaker[AsyncSession]


async def _open(api: Api, agent: AgentRunner, message: str) -> dict[str, Any]:
    response = await api.post("/chat/tickets", "customer", {"message": message})
    assert response.status_code == 201, response.text
    await agent.wait()
    ticket: dict[str, Any] = response.json()["ticket"]
    return ticket


async def _paused_ticket(api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM) -> str:
    scripted_llm.queue(intake(), resolution())
    ticket = await _open(api, agent, "My headphones arrived damaged, I want a refund")
    return str(ticket["id"])


async def _approval_id(api: Api, ticket_id: str) -> str:
    approvals = (await api.get("/approvals", "supervisor")).json()["items"]
    [approval] = [a for a in approvals if a["ticket_id"] == ticket_id]
    return str(approval["id"])


# --- Live stream over real HTTP ----------------------------------------------------------


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[str]:
    """Run the app on a real local port, so the event stream is read over HTTP."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:  # uvicorn exposes no startup event
            if task.done():
                task.result()  # surface a startup failure instead of waiting
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def _read_events(response: httpx.Response, until: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    name = None
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: ") and name:
            events.append((name, json.loads(line.removeprefix("data: "))))
            if name == until:
                return events
    raise AssertionError(f"Stream ended before {until}")


async def test_stream_emits_step_events_in_order_over_http(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    scripted_llm.queue(intake(), resolution())
    async with serve(api.app) as base_url, httpx.AsyncClient(base_url=base_url, timeout=10) as http:
        created = await http.post(
            "/api/chat/tickets",
            json={"message": "My headphones arrived damaged, I want a refund"},
            headers=api.auth("customer"),
        )
        ticket_id = created.json()["ticket"]["id"]
        async with http.stream(
            "GET", f"/api/chat/tickets/{ticket_id}/stream", headers=api.auth("customer")
        ) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            events = await asyncio.wait_for(
                _read_events(response, until="approval_requested"), timeout=10
            )
        await agent.wait()

    steps = [data["step"] for name, data in events if name == "step_started"]
    assert steps == [
        "intake",
        "customer_data",
        "policy_rag",
        "resolution",
        "policy_gate",
        "request_approval",
    ]
    assert events[0] == ("step_started", {"step": "intake"})
    assert events[-1][1]["amount"] == "79.99"
    assert any(
        name == "message" and "asked a supervisor" in data["content"] for name, data in events
    )


# --- Access control -----------------------------------------------------------------------


async def test_staff_cannot_open_chat_tickets(api: Api) -> None:
    response = await api.post("/chat/tickets", "agent", {"message": "hello"})
    assert response.status_code == 403


@pytest.mark.parametrize("message", ["", "x" * 2001])
async def test_message_length_is_validated(api: Api, message: str) -> None:
    response = await api.post("/chat/tickets", "customer", {"message": message})
    assert response.status_code == 422


async def test_customers_see_only_their_own_tickets(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)

    for request in (
        api.get(f"/tickets/{ticket_id}", "other_customer"),
        api.post(f"/chat/tickets/{ticket_id}/messages", "other_customer", {"message": "hi"}),
        api.get(f"/chat/tickets/{ticket_id}/stream", "other_customer"),
    ):
        response = await request
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ticket_not_found"

    assert (await api.get("/tickets", "other_customer")).json()["total"] == 0
    assert (await api.get("/tickets", "customer")).json()["total"] == 1


async def test_customer_view_hides_internal_detail(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    detail = (await api.get(f"/tickets/{ticket_id}", "customer")).json()
    assert len(detail["messages"]) == 2
    assert (detail["approvals"], detail["trace"], detail["customer_context"]) == ([], [], None)


# --- Staff views ----------------------------------------------------------------------------


async def test_staff_ticket_detail_has_trace_approvals_and_context(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    detail = (await api.get(f"/tickets/{ticket_id}", "agent")).json()

    assert detail["ticket"]["status"] == "awaiting_approval"
    assert detail["ticket"]["summary"]
    assert detail["approvals"][0]["proposed_action"]["amount"] == "79.99"
    actions = [entry["action"] for entry in detail["trace"]]
    assert "policy_decision" in actions and "approval_requested" in actions
    context = detail["customer_context"]
    assert context["customer"]["name"] == "Jordan Lee"
    assert "email" not in context["customer"]
    assert context["order"]["order_id"] == "ORD-10001"
    assert len(context["recent_orders"]) == 5


async def test_staff_list_filters_and_searches(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    paused_id = await _paused_ticket(api, agent, scripted_llm)
    scripted_llm.queue(intake(orders=["ORD-10002"]), resolution())
    await _open(api, agent, "My phone case cracked")

    everything = (await api.get("/tickets", "agent")).json()
    assert everything["total"] == 2
    waiting = (await api.get("/tickets", "agent", status="awaiting_approval")).json()
    assert [t["id"] for t in waiting["items"]] == [paused_id]
    by_order = (await api.get("/tickets", "agent", q="ord-10002")).json()
    assert [t["order_id"] for t in by_order["items"]] == ["ORD-10002"]
    page = (await api.get("/tickets", "agent", page=2, page_size=1)).json()
    assert (page["total"], len(page["items"]), page["page"]) == (2, 1, 2)


# --- Approvals --------------------------------------------------------------------------


async def test_supervisor_approval_resumes_and_refunds_once(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, mock_store: Any
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    approval_id = await _approval_id(api, ticket_id)

    first = await api.post(
        f"/approvals/{approval_id}/decision", "supervisor", {"decision": "approve"}
    )
    second = await api.post(
        f"/approvals/{approval_id}/decision", "supervisor", {"decision": "approve"}
    )
    await agent.wait()

    assert first.status_code == 200 and first.json()["status"] == "approved"
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "approval_already_decided"
    refunds = [r for r in mock_store.refunds.values() if r.order_id == "ORD-10001"]
    assert len(refunds) == 1
    detail = (await api.get(f"/tickets/{ticket_id}", "customer")).json()
    assert detail["ticket"]["status"] == "resolved"
    assert "I've refunded $79.99" in detail["messages"][-1]["content"]


async def test_rejection_resumes_without_refunding(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, mock_store: Any
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    approval_id = await _approval_id(api, ticket_id)

    response = await api.post(
        f"/approvals/{approval_id}/decision",
        "supervisor",
        {"decision": "reject", "note": "Photos show wear, not damage"},
    )
    await agent.wait()

    assert response.json()["note"] == "Photos show wear, not damage"
    assert not [r for r in mock_store.refunds.values() if r.order_id == "ORD-10001"]
    detail = (await api.get(f"/tickets/{ticket_id}", "agent")).json()
    assert detail["ticket"]["status"] == "rejected"
    decision = next(e for e in detail["trace"] if e["action"] == "approval_decision")
    assert decision["status"] == "rejected"
    assert "Photos show wear" not in detail["messages"][-1]["content"]  # notes stay internal


@pytest.mark.parametrize("who", ["agent", "customer"])
async def test_only_supervisors_decide(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, who: str
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    approval_id = await _approval_id(api, ticket_id)
    response = await api.post(f"/approvals/{approval_id}/decision", who, {"decision": "approve"})
    assert response.status_code == 403


async def test_agents_can_view_the_approval_queue(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    queue = (await api.get("/approvals", "agent")).json()
    assert [a["ticket_id"] for a in queue["items"]] == [ticket_id]
    assert queue["items"][0]["ticket_public_id"].startswith("TCK-")
    assert (await api.get("/approvals", "customer")).status_code == 403


async def test_unknown_approval_is_404(api: Api) -> None:
    response = await api.post(
        f"/approvals/{uuid.uuid4()}/decision", "supervisor", {"decision": "approve"}
    )
    assert response.status_code == 404


# --- Follow-up messages ---------------------------------------------------------------------


async def test_follow_up_while_awaiting_approval_gets_a_fixed_reply_and_no_run(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, live_sessions: Sessions
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    model_calls, runs = len(scripted_llm.calls), await count(live_sessions, AgentRun)

    response = await api.post(
        f"/chat/tickets/{ticket_id}/messages",
        "customer",
        {"message": "The left ear cup is cracked"},
    )
    await agent.wait()

    customer, reply = response.json()["messages"]
    assert customer["content"] == "The left ear cup is cracked"
    assert "supervisor is reviewing it" in reply["content"]
    assert len(scripted_llm.calls) == model_calls
    assert await count(live_sessions, AgentRun) == runs
    detail = (await api.get(f"/tickets/{ticket_id}", "supervisor")).json()
    assert detail["ticket"]["status"] == "awaiting_approval"
    assert "The left ear cup is cracked" in [m["content"] for m in detail["messages"]]


async def test_follow_up_on_a_closed_ticket_is_flagged_and_answered_once(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, live_sessions: Sessions
) -> None:
    scripted_llm.queue(intake(orders=["ORD-10002"]), resolution())
    ticket_id = (await _open(api, agent, "My phone case cracked"))["id"]
    runs = await count(live_sessions, AgentRun)

    first = await api.post(
        f"/chat/tickets/{ticket_id}/messages", "customer", {"message": "Thanks!"}
    )
    second = await api.post(
        f"/chat/tickets/{ticket_id}/messages", "customer", {"message": "Also..."}
    )
    await agent.wait()

    assert len(first.json()["messages"]) == 2
    assert "start a new request" in first.json()["messages"][1]["content"]
    assert len(second.json()["messages"]) == 1  # no second templated reply
    assert await count(live_sessions, AgentRun) == runs
    detail = (await api.get(f"/tickets/{ticket_id}", "agent")).json()
    assert detail["ticket"]["status"] == "resolved"
    assert detail["ticket"]["customer_replied"] is True
    listed = (await api.get("/tickets", "agent")).json()["items"][0]
    assert listed["customer_replied"] is True


async def test_follow_up_on_an_open_ticket_continues_the_conversation(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    scripted_llm.queue(intake(orders=[]), intake(orders=["ORD-10001"]), resolution())
    ticket_id = (await _open(api, agent, "Something I ordered arrived damaged"))["id"]

    await api.post(f"/chat/tickets/{ticket_id}/messages", "customer", {"message": "The headphones"})
    await agent.wait()

    detail = (await api.get(f"/tickets/{ticket_id}", "customer")).json()
    assert detail["ticket"]["status"] == "awaiting_approval"
    assert detail["ticket"]["order_id"] == "ORD-10001"


async def test_missing_agent_runtime_is_a_clean_503(api: Api) -> None:
    del api.app.dependency_overrides[get_agent_runner]  # as if the lifespan never started it
    response = await api.post("/chat/tickets", "customer", {"message": "hello"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


# --- Audit log and list enrichment ------------------------------------------------------


async def test_audit_log_is_newest_first_with_readable_actors(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    approval_id = await _approval_id(api, ticket_id)
    await api.post(f"/approvals/{approval_id}/decision", "supervisor", {"decision": "approve"})
    await agent.wait()
    public_id = (await api.get(f"/tickets/{ticket_id}", "agent")).json()["ticket"]["public_id"]

    by_number = (await api.get("/audit", "agent", ticket_id=public_id)).json()
    by_uuid = (await api.get("/audit", "agent", ticket_id=ticket_id)).json()
    assert by_number["total"] == by_uuid["total"] > 10
    entries = by_number["items"]
    times = [e["created_at"] for e in entries]
    assert times == sorted(times, reverse=True)
    decision = next(e for e in entries if e["action"] == "approval_decision")
    assert decision["actor_label"] == "supervisor@acme.com"
    assert {e["ticket_public_id"] for e in entries} == {public_id}
    assert next(e for e in entries if e["action"] == "policy_decision")["actor_label"] == "agent"


async def test_audit_log_is_staff_only_and_rejects_unknown_tickets(api: Api) -> None:
    assert (await api.get("/audit", "customer")).status_code == 403
    assert (await api.get("/audit", "agent", ticket_id="TCK-999999")).status_code == 404
    assert (await api.get("/audit", "agent", ticket_id="not-a-ticket")).status_code == 404


async def test_ticket_list_includes_customer_names(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    await _paused_ticket(api, agent, scripted_llm)
    [item] = (await api.get("/tickets", "agent")).json()["items"]
    assert item["customer_name"] == "Jordan Lee"


async def test_staff_see_cost_and_latency_per_ticket(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id = await _paused_ticket(api, agent, scripted_llm)
    approval_id = await _approval_id(api, ticket_id)
    await api.post(f"/approvals/{approval_id}/decision", "supervisor", {"decision": "approve"})
    await agent.wait()

    usage = (await api.get(f"/tickets/{ticket_id}", "agent")).json()["usage"]
    assert usage["runs"] == 2  # paused, then resumed
    assert (usage["input_tokens"], usage["output_tokens"]) == (200, 40)
    assert usage["cost_usd"] == "0.000160"
    assert usage["latency_ms"] >= 0
    assert usage["last_run_status"] == "completed"
