"""Reliability: no double refunds, clean failure when things hang or keep failing."""

import asyncio
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx
from langchain_core.messages import BaseMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runner import AgentRunner
from app.agents.runtime import EventType
from app.agents.tools.commerce import CircuitBreaker, CommerceClient
from app.core.config import get_settings
from app.core.security import Role, hash_password
from app.db.models import AgentRun, AgentRunStatus, Approval, TicketStatus, User
from app.observability.cost import UsageTracker
from app.services import approval_service
from tests.agent_support import (
    Api,
    ScriptedLLM,
    agent_messages,
    audit_trail,
    intake,
    load_ticket,
    open_ticket,
    resolution,
    runs_for,
)

Sessions = async_sessionmaker[AsyncSession]
CUSTOMER = "cus_001"


def refunds_for(store: Any, order_id: str) -> list[Any]:
    return [
        r for r in store.refunds.values() if r.order_id == order_id and "seed" not in r.refund_id
    ]


async def _approve_in_db(sessions: Sessions, ticket_id: uuid.UUID) -> None:
    async with sessions() as session:
        supervisor = User(
            email=f"sup-{uuid.uuid4().hex[:6]}@acme.com",
            password_hash=hash_password("x", rounds=4),
            role=Role.SUPERVISOR,
        )
        session.add(supervisor)
        await session.flush()
        approval = await session.scalar(select(Approval).where(Approval.ticket_id == ticket_id))
        assert approval is not None
        await approval_service.decide(
            session, approval.id, approve=True, decided_by=supervisor.id, note=None
        )
        await session.commit()


async def _paused(agent: AgentRunner, llm: ScriptedLLM, sessions: Sessions) -> uuid.UUID:
    llm.queue(intake(), resolution())
    ticket_id = await open_ticket(sessions, "My headphones arrived damaged, I want a refund")
    assert await agent.start(ticket_id, CUSTOMER) is AgentRunStatus.PAUSED
    return ticket_id


async def test_a_duplicate_resume_does_nothing(
    agent: AgentRunner, scripted_llm: ScriptedLLM, live_sessions: Sessions, mock_store: Any
) -> None:
    ticket_id = await _paused(agent, scripted_llm, live_sessions)
    await _approve_in_db(live_sessions, ticket_id)

    assert await agent.resume(ticket_id, CUSTOMER) is AgentRunStatus.COMPLETED
    assert await agent.resume(ticket_id, CUSTOMER) is None

    assert len(refunds_for(mock_store, "ORD-10001")) == 1
    assert len(await runs_for(live_sessions, ticket_id)) == 2  # the pause and one resume


async def test_concurrent_resumes_refund_once(
    agent: AgentRunner, scripted_llm: ScriptedLLM, live_sessions: Sessions, mock_store: Any
) -> None:
    ticket_id = await _paused(agent, scripted_llm, live_sessions)
    await _approve_in_db(live_sessions, ticket_id)

    results = await asyncio.gather(*(agent.resume(ticket_id, CUSTOMER) for _ in range(3)))

    assert sorted(results, key=str) == sorted([AgentRunStatus.COMPLETED, None, None], key=str)
    assert len(refunds_for(mock_store, "ORD-10001")) == 1


async def test_double_click_approve_refunds_once(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, mock_store: Any
) -> None:
    scripted_llm.queue(intake(), resolution())
    await api.post("/chat/tickets", "customer", {"message": "My headphones arrived damaged"})
    await agent.wait()
    [approval] = (await api.get("/approvals", "supervisor")).json()["items"]

    clicks = await asyncio.gather(
        *(
            api.post(f"/approvals/{approval['id']}/decision", "supervisor", {"decision": "approve"})
            for _ in range(2)
        )
    )
    await agent.wait()

    assert sorted(r.status_code for r in clicks) == [200, 409]
    assert len(refunds_for(mock_store, "ORD-10001")) == 1


class SlowLLM(ScriptedLLM):
    async def generate(
        self, schema: type[Any], messages: list[BaseMessage], usage: UsageTracker
    ) -> Any:
        await asyncio.sleep(5)
        return await super().generate(schema, messages, usage)


async def test_a_hung_run_is_cut_off_and_handed_to_a_human(
    make_agent: Callable[..., AgentRunner], live_sessions: Sessions
) -> None:
    agent = make_agent(
        settings=get_settings().model_copy(update={"agent_run_timeout_seconds": 0.3}),
        llm=SlowLLM(),
    )
    ticket_id = await open_ticket(live_sessions, "My headphones arrived damaged")

    assert await agent.start(ticket_id, CUSTOMER) is AgentRunStatus.FAILED

    assert (await load_ticket(live_sessions, ticket_id)).status is TicketStatus.ESCALATED
    assert (
        "something went wrong on our side" in (await agent_messages(live_sessions, ticket_id))[-1]
    )
    assert agent.events.history(str(ticket_id))[-1].type is EventType.ERROR
    [failure] = await audit_trail(live_sessions, ticket_id, "run_failed")
    assert failure.output["error"] == "TimeoutError"


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner, self.calls = inner, 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return await self.inner.handle_async_request(request)


async def test_open_circuit_escalates_without_calling_the_shop(
    make_agent: Callable[..., AgentRunner],
    live_sessions: Sessions,
    mock_commerce: Any,
    mock_store: Any,
) -> None:
    failing_app = mock_commerce.create_app(
        mock_commerce.MockSettings(failure_rate=1.0, slow_fraction=0.0, slow_seconds=0.0),
        mock_store,
    )
    transport = CountingTransport(httpx.ASGITransport(app=failing_app))
    agent = make_agent(
        commerce=CommerceClient(
            "http://mock-commerce",
            timeout_seconds=1,
            max_attempts=2,
            backoff_seconds=0,
            breaker=CircuitBreaker(threshold=1, cooldown=60),
            transport=transport,
        )
    )
    first = await open_ticket(live_sessions, "My headphones arrived damaged")
    await agent.start(first, CUSTOMER)
    calls_after_first = transport.calls

    second = await open_ticket(live_sessions, "Where is my watch?")
    await agent.start(second, CUSTOMER)

    assert agent.commerce.breaker.state.value == "open"
    assert transport.calls == calls_after_first  # failed fast, no request sent
    for ticket_id in (first, second):
        assert (await load_ticket(live_sessions, ticket_id)).status is TicketStatus.ESCALATED
    [blocked] = [
        e for e in await audit_trail(live_sessions, second, "tool_call") if e.status == "failure"
    ]
    assert blocked.output["code"] == "circuit_open"
    await agent.commerce.aclose()


async def test_half_the_calls_failing_still_ends_every_ticket_cleanly(
    make_agent: Callable[..., AgentRunner],
    scripted_llm: ScriptedLLM,
    live_sessions: Sessions,
    mock_store: Any,
) -> None:
    """The acceptance scenario in-process: MOCK_FAILURE_RATE=0.5 with no duplicate refunds."""
    agent = make_agent(failure_rate=0.5, slow_fraction=0.0, random_seed=5)
    requests = [
        "Refund ORD-10002, the case cracked",
        "Refund ORD-10002 please, it is broken",  # a second refund on the same order
        "Refund ORD-10008, the lamp flickers",
        "Refund ORD-10008 again please",
        "Refund ORD-10019, the pad is torn",  # someone else's order
        "Refund ORD-10002 one more time",
    ]
    for _ in requests:
        scripted_llm.queue(intake(orders=[]), resolution())

    outcomes = []
    for message in requests:
        ticket_id = await open_ticket(live_sessions, message)
        await agent.start(ticket_id, CUSTOMER)
        outcomes.append((await load_ticket(live_sessions, ticket_id)).status)

    assert set(outcomes) <= {TicketStatus.RESOLVED, TicketStatus.REJECTED, TicketStatus.ESCALATED}
    # The seed makes the faults deterministic: some requests survive them, some escalate.
    assert TicketStatus.RESOLVED in outcomes
    assert TicketStatus.ESCALATED in outcomes
    per_order = Counter(
        r.order_id for r in mock_store.refunds.values() if "seed" not in r.refund_id
    )
    assert all(count == 1 for count in per_order.values()), per_order
    async with live_sessions() as session:
        statuses = set(await session.scalars(select(AgentRun.status)))
    assert AgentRunStatus.FAILED not in statuses  # failures were handled, not crashes
