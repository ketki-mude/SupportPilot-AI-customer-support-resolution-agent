"""Request ids, security headers, body limits, timeouts, CORS, and rate limits."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agents.runner import AgentRunner
from app.core.config import get_settings
from app.core.middleware import install_middleware
from app.core.rate_limit import RateLimits, SlidingWindowLimiter
from app.main import create_app
from tests.agent_support import Api, ScriptedLLM, intake, resolution
from tests.conftest import TEST_PASSWORD


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, object]]:
    return [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg.get("event") == name]


# --- Request ids and the access log ---------------------------------------------------


async def test_every_response_carries_a_request_id(api: Api) -> None:
    response = await api.client.get("/api/health")
    assert len(response.headers["x-request-id"]) == 32


async def test_a_valid_incoming_request_id_is_kept_and_logged(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = await api.client.get("/api/health", headers={"X-Request-ID": "trace-abc.123"})
    assert response.headers["x-request-id"] == "trace-abc.123"
    [line] = [e for e in _events(caplog, "request_completed") if e.get("path") == "/api/health"]
    assert line["request_id"] == "trace-abc.123"
    assert line["status"] == 200
    assert line["method"] == "GET"


@pytest.mark.parametrize("bad", ["has spaces", "x" * 65, "semi;colon"])
async def test_unsafe_request_ids_are_replaced(api: Api, bad: str) -> None:
    response = await api.client.get("/api/health", headers={"X-Request-ID": bad})
    assert response.headers["x-request-id"] != bad


async def test_background_agent_logs_carry_the_request_id(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    scripted_llm.queue(intake(orders=["ORD-10002"]), resolution())
    with caplog.at_level(logging.INFO):
        await api.client.post(
            "/api/chat/tickets",
            json={"message": "My phone case cracked"},
            headers={**api.auth("customer"), "X-Request-ID": "req-from-customer"},
        )
        await agent.wait()
    [recorded] = _events(caplog, "agent_run_recorded")
    assert recorded["request_id"] == "req-from-customer"
    assert recorded["ticket_id"]


# --- Security headers ------------------------------------------------------------------


async def test_api_responses_have_security_headers(api: Api) -> None:
    headers = (await api.client.get("/api/health")).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "strict-transport-security" not in headers


async def test_docs_page_keeps_working_without_the_strict_csp(api: Api) -> None:
    response = await api.client.get("/api/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_hsts_is_sent_in_production() -> None:
    app = create_app(get_settings().model_copy(update={"app_env": "production"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/auth/me")
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")


# --- Body size limit ---------------------------------------------------------------------


async def test_oversized_body_is_refused(api: Api) -> None:
    response = await api.client.post(
        "/api/auth/login",
        content=b'{"email": "a@acme.com", "password": "' + b"x" * 70_000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_oversized_chunked_body_without_length_is_refused(api: Api) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"x" * 8_192

    response = await api.client.post(
        "/api/chat/tickets", content=chunks(), headers=api.auth("customer")
    )
    assert response.status_code == 413


async def test_normal_bodies_pass(api: Api) -> None:
    response = await api.client.post(
        "/api/auth/login", json={"email": "agent@acme.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200


# --- Request timeout ---------------------------------------------------------------------


def _slow_app(timeout: float) -> FastAPI:
    app = FastAPI()
    install_middleware(app, get_settings().model_copy(update={"request_timeout_seconds": timeout}))

    @app.get("/slow")
    async def slow() -> dict[str, str]:
        await asyncio.sleep(1)
        return {"ok": "yes"}

    @app.get("/tickets/{ticket_id}/stream")
    async def stream(ticket_id: str) -> dict[str, str]:
        await asyncio.sleep(0.2)
        return {"ticket": ticket_id}

    return app


async def test_slow_requests_time_out_with_a_clean_504() -> None:
    async with AsyncClient(transport=ASGITransport(app=_slow_app(0.05)), base_url="http://t") as c:
        response = await c.get("/slow")
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "request_timeout"


async def test_event_streams_are_not_cut_off_by_the_timeout() -> None:
    async with AsyncClient(transport=ASGITransport(app=_slow_app(0.05)), base_url="http://t") as c:
        response = await c.get("/tickets/t1/stream")
    assert response.status_code == 200


# --- CORS ------------------------------------------------------------------------------


async def _preflight(origin: str) -> dict[str, str]:
    app = create_app(get_settings().model_copy(update={"cors_origins": ["https://app.example"]}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/api/auth/login",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        )
    return dict(response.headers)


async def test_cors_allows_configured_origins_only() -> None:
    allowed = await _preflight("https://app.example")
    assert allowed["access-control-allow-origin"] == "https://app.example"
    assert allowed["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in await _preflight("https://evil.example")


async def test_no_cors_headers_by_default(api: Api) -> None:
    response = await api.client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


# --- Rate limits -------------------------------------------------------------------------


def _tight_limits(api: Api, per_minute: int = 2) -> None:
    api.app.state.rate_limits = RateLimits(
        chat=SlidingWindowLimiter(per_minute), login=SlidingWindowLimiter(per_minute)
    )


async def test_chat_messages_are_rate_limited_per_customer(api: Api) -> None:
    _tight_limits(api)
    path = f"/chat/tickets/{uuid.uuid4()}/messages"
    statuses = [(await api.post(path, "customer", {"message": "hi"})).status_code for _ in range(3)]
    assert statuses == [404, 404, 429]  # the limit applies before anything else happens
    limited = await api.post(path, "customer", {"message": "hi"})
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1
    # Another customer has their own allowance.
    assert (await api.post(path, "other_customer", {"message": "hi"})).status_code == 404


async def test_login_attempts_are_rate_limited_per_account(api: Api) -> None:
    _tight_limits(api)
    body = {"email": "agent@acme.com", "password": "wrong-password"}
    statuses = [(await api.client.post("/api/auth/login", json=body)).status_code for _ in range(3)]
    assert statuses == [401, 401, 429]
    other = await api.client.post(
        "/api/auth/login", json={"email": "supervisor@acme.com", "password": TEST_PASSWORD}
    )
    assert other.status_code == 200
