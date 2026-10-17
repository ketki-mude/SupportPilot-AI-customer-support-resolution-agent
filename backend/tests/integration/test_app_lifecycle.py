"""The app's startup and shutdown, middleware edge cases, and degraded ticket detail."""

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.runner import AgentRunner
from app.agents.tools.commerce import CommerceClient
from app.core.config import get_settings
from app.core.middleware import RequestContextMiddleware, RequestTimeoutMiddleware
from app.main import create_app
from tests.agent_support import Api, ScriptedLLM, intake, resolution


async def test_lifespan_starts_and_stops_the_agent_runtime(
    database_url: str, policy_index: None
) -> None:
    app = create_app(get_settings().model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        runner = app.state.agent_runner
        assert isinstance(runner, AgentRunner)
        assert runner.commerce.breaker.state.value == "closed"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            health = (await client.get("/api/health")).json()
        assert health["commerce_circuit"] == "closed"


@pytest.mark.parametrize("middleware", [RequestContextMiddleware, RequestTimeoutMiddleware])
async def test_middleware_passes_non_http_scopes_through(middleware: Any) -> None:
    seen = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    wrapped = (
        middleware(inner)
        if middleware is RequestContextMiddleware
        else middleware(inner, seconds=1)
    )
    await wrapped({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


async def test_timeout_after_the_response_started_does_not_send_a_second_response() -> None:
    sent: list[dict[str, Any]] = []

    async def slow_streamer(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(1)

    async def capture(message: dict[str, Any]) -> None:
        sent.append(message)

    await RequestTimeoutMiddleware(slow_streamer, seconds=0.05)(
        {"type": "http", "path": "/slow", "method": "GET", "headers": []}, None, capture
    )
    assert [m["type"] for m in sent] == ["http.response.start"]


async def test_ticket_detail_survives_a_commerce_outage(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    scripted_llm.queue(intake(), resolution())
    created = await api.post(
        "/chat/tickets", "customer", {"message": "My headphones arrived damaged"}
    )
    await agent.wait()
    ticket_id = created.json()["ticket"]["id"]
    agent.commerce = CommerceClient(
        "http://unreachable.invalid", timeout_seconds=0.2, max_attempts=1, backoff_seconds=0
    )

    detail = (await api.get(f"/tickets/{ticket_id}", "agent")).json()
    listed = (await api.get("/tickets", "agent")).json()["items"][0]

    assert detail["customer_context"] is None
    assert "unavailable" in detail["customer_context_error"]
    assert detail["messages"]  # the rest still loads
    assert listed["customer_name"] is None  # names are optional in the list
    await agent.commerce.aclose()
