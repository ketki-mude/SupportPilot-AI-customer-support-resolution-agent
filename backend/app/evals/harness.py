"""Run the real agent graph over evaluation cases, each one isolated.

Isolation: every run rebuilds its own `<db>_eval` database, so demo tickets are never touched;
every case first resets the dedicated evaluation shop to that case's fixture and faults; every
case is a new ticket on a new thread.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
from alembic import command
from alembic.config import Config
from langchain_core.embeddings import Embeddings
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.agents.llm import StructuredGenerator, StructuredLLM, create_chat_model
from app.agents.runner import AgentGraph, AgentRunner
from app.agents.runtime import EventBus
from app.agents.tools.commerce import CircuitBreaker, CommerceClient
from app.core.config import Settings
from app.core.security import Role, hash_password
from app.db.models import AgentRun, AgentRunStatus, Approval, ApprovalStatus, AuditLog, Ticket, User
from app.evals.dataset import Dataset, EvalCase
from app.evals.metrics import CaseResult, Observation, ShopEffect, aggregate, score
from app.evals.mock_llm import KeywordLLM
from app.evals.quota import RetryingLLM
from app.evals.report import RunReport
from app.rag.ingest import ingest_policies
from app.services import approval_service, ticket_service
from app.services.audit_service import AuditService

logger = structlog.get_logger(__name__)

LlmMode = Literal["live", "mock"]
Progress = Callable[[int, int, CaseResult], Awaitable[None]]

BACKEND_DIR = Path(__file__).resolve().parents[2]
EVAL_SUPERVISOR_EMAIL = "eval-supervisor@acme.com"
DEFAULT_FAULTS: dict[str, Any] = {
    "failure_rate": 0.0,
    "slow_fraction": 0.5,
    "slow_seconds": 2.0,
    "random_seed": 7,
}
# Typical tokens per case across its two model calls, measured on development runs. Only used
# to estimate the cost before a live run; the report records the actual tokens.
TYPICAL_INPUT_TOKENS = 1_700
TYPICAL_OUTPUT_TOKENS = 900


def evals_dir(settings: Settings) -> Path:
    """The dataset and saved reports: EVAL_DIR, or the evals/ folder next to the backend."""
    return Path(settings.eval_dir) if settings.eval_dir else BACKEND_DIR.parent / "evals"


def estimate_cost(cases: int, settings: Settings) -> Decimal:
    per_case = (
        TYPICAL_INPUT_TOKENS * settings.llm_input_usd_per_million
        + TYPICAL_OUTPUT_TOKENS * settings.llm_output_usd_per_million
    ) / Decimal(1_000_000)
    return (per_case * cases).quantize(Decimal("0.0001"))


def eval_database_url(settings: Settings) -> str:
    if settings.eval_database_url:
        return settings.eval_database_url
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_eval").render_as_string(hide_password=False)


def build_llm(mode: LlmMode, settings: Settings) -> StructuredGenerator:
    """The configured model with quota backoff, or the offline stand-in."""
    if mode == "mock":
        return KeywordLLM()
    return RetryingLLM(StructuredLLM(partial(create_chat_model, settings)))


async def prepare_database(url: str, embeddings: Embeddings) -> None:
    """Drop and recreate the evaluation database, migrate it, and load the policies."""
    target = make_url(url)
    name = target.database or ""
    if not name.endswith("_eval"):
        raise RuntimeError(f"Refusing to recreate {name!r}: evaluation databases end in _eval")
    admin = create_async_engine(
        target.set(database="postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as connection:
        quoted = connection.dialect.identifier_preparer.quote(name)
        await connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
        await connection.execute(text(f"CREATE DATABASE {quoted}"))
    await admin.dispose()

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_async_engine(url, poolclass=NullPool)
    async with async_sessionmaker(engine)() as session:
        await ingest_policies(session, embeddings)
        session.add(
            User(
                email=EVAL_SUPERVISOR_EMAIL,
                password_hash=hash_password(uuid.uuid4().hex, rounds=4),
                role=Role.SUPERVISOR,
            )
        )
        await session.commit()
    await engine.dispose()


@dataclass
class EvalRun:
    settings: Settings
    embeddings: Embeddings
    llm: StructuredGenerator
    transport: httpx.AsyncBaseTransport | None = None
    results: list[CaseResult] = field(default_factory=list)
    tokens: list[int] = field(default_factory=lambda: [0, 0])
    stopped_reason: str | None = None

    async def run(self, cases: Sequence[EvalCase], on_progress: Progress | None = None) -> None:
        url = eval_database_url(self.settings)
        await prepare_database(url, self.embeddings)
        engine = create_async_engine(url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with (
                open_checkpointer(url) as saver,
                httpx.AsyncClient(
                    base_url=self.settings.eval_commerce_url, transport=self.transport, timeout=30
                ) as shop,
            ):
                graph = build_graph().compile(checkpointer=saver)
                supervisor_id = await self._supervisor_id(sessions)
                for index, case in enumerate(cases, start=1):
                    observation = await self._run_case(case, graph, sessions, shop, supervisor_id)
                    self.tokens[0] += observation.input_tokens
                    self.tokens[1] += observation.output_tokens
                    exhausted = getattr(self.llm, "exhausted", None)
                    if exhausted:
                        # This case ran into the quota, so its result says nothing about the
                        # agent. It is left out, and the report is marked incomplete.
                        self.stopped_reason = (
                            f"{exhausted}. Stopped at case {index} ({case.id}), which is not "
                            f"scored; {index - 1} of {len(cases)} cases were scored."
                        )
                        break
                    result = score(case, observation)
                    self.results.append(result)
                    if on_progress:
                        await on_progress(index, len(cases), result)
        finally:
            await engine.dispose()

    @staticmethod
    async def _supervisor_id(sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
        async with sessions() as session:
            supervisor = await session.scalar(
                select(User).where(User.email == EVAL_SUPERVISOR_EMAIL)
            )
            if supervisor is None:
                raise RuntimeError("The evaluation database has no supervisor")
            return supervisor.id

    async def _run_case(
        self,
        case: EvalCase,
        graph: AgentGraph,
        sessions: async_sessionmaker[AsyncSession],
        shop: httpx.AsyncClient,
        supervisor_id: uuid.UUID,
    ) -> Observation:
        reset = await shop.post(
            "/admin/reset", json={"fixture": case.fixture, "faults": DEFAULT_FAULTS | case.faults}
        )
        reset.raise_for_status()
        settings = self.settings
        commerce = CommerceClient(
            settings.eval_commerce_url,
            timeout_seconds=settings.commerce_timeout_seconds,
            max_attempts=settings.commerce_max_attempts,
            backoff_seconds=settings.commerce_backoff_seconds,
            breaker=CircuitBreaker(
                settings.commerce_breaker_threshold, settings.commerce_breaker_cooldown_seconds
            ),
            transport=self.transport,
        )
        runner = AgentRunner(
            graph=graph,
            session_factory=sessions,
            commerce=commerce,
            audit=AuditService(sessions),
            llm=self.llm,
            embeddings=self.embeddings,
            settings=settings,
            events=EventBus(),
        )
        async with sessions() as session:
            ticket = await ticket_service.create_ticket(
                session, customer_id=case.customer_id, first_message=case.customer_message
            )
            await session.commit()
            ticket_id = ticket.id

        started = time.perf_counter()
        status = await runner.start(ticket_id, case.customer_id)
        if status is AgentRunStatus.PAUSED:
            await self._decide(
                sessions, ticket_id, supervisor_id, approve=case.supervisor == "approve"
            )
            await runner.resume(ticket_id, case.customer_id)
        logger.info(
            "eval_case_finished", case=case.id, seconds=round(time.perf_counter() - started, 2)
        )
        await commerce.aclose()
        return await observe(case, ticket_id, sessions, shop)

    @staticmethod
    async def _decide(
        sessions: async_sessionmaker[AsyncSession],
        ticket_id: uuid.UUID,
        supervisor_id: uuid.UUID,
        *,
        approve: bool,
    ) -> None:
        async with sessions() as session:
            approval = await session.scalar(
                select(Approval).where(
                    Approval.ticket_id == ticket_id, Approval.status == ApprovalStatus.PENDING
                )
            )
            if approval is None:
                return
            await approval_service.decide(
                session, approval.id, approve=approve, decided_by=supervisor_id, note="Evaluation"
            )
            await session.commit()


async def observe(
    case: EvalCase,
    ticket_id: uuid.UUID,
    sessions: async_sessionmaker[AsyncSession],
    shop: httpx.AsyncClient,
) -> Observation:
    """Read back what the agent did, from the database and from the shop itself."""
    async with sessions() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            raise RuntimeError(f"Ticket for {case.id} disappeared")
        audit = list(
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.ticket_id == ticket_id)
                .order_by(AuditLog.created_at)
            )
        )
        approvals = list(
            await session.scalars(
                select(Approval)
                .where(Approval.ticket_id == ticket_id)
                .order_by(Approval.created_at)
            )
        )
        runs = list(await session.scalars(select(AgentRun).where(AgentRun.ticket_id == ticket_id)))

    decisions = [e for e in audit if e.action == "policy_decision"]
    decision = decisions[-1].output if decisions else {}
    escalated = [e for e in audit if e.action == "escalated"]
    failures = [e for e in audit if e.action == "run_failed"]
    tool_calls = [e for e in audit if e.action == "tool_call"]
    clarified = any(e.action == "step:clarify" and "clarification_turns" in e.output for e in audit)

    state = (await shop.get("/admin/state")).json()
    return Observation(
        ticket_status=ticket.status.value,
        intent=ticket.category.value if ticket.category else None,
        decision=decision.get("decision"),
        decided_action=decision.get("action"),
        rule=decision.get("rule"),
        approval_requested=bool(approvals),
        approval_status=approvals[-1].status.value if approvals else None,
        clarified=clarified,
        escalation_cause=escalated[-1].output.get("cause") if escalated else None,
        effects=shop_effects(case, state),
        tool_calls=len(tool_calls),
        tool_successes=sum(1 for e in tool_calls if e.status == "success"),
        latency_ms=sum(run.latency_ms or 0 for run in runs),
        cost_usd=sum((run.cost_usd for run in runs), Decimal(0)),
        input_tokens=sum(run.input_tokens for run in runs),
        output_tokens=sum(run.output_tokens for run in runs),
        error=failures[-1].output.get("message") if failures else None,
    )


def shop_effects(case: EvalCase, state: dict[str, Any]) -> list[ShopEffect]:
    """Refunds, replacements, and cancellations made during the case, beyond the fixture's."""
    owners = {order["order_id"]: order["customer_id"] for order in state["orders"]}
    prior_refunds = {r["refund_id"] for r in case.fixture.get("prior_refunds", [])}
    prior_replacements = {r["replacement_id"] for r in case.fixture.get("prior_replacements", [])}
    were_cancelled = {o["order_id"] for o in case.fixture["orders"] if o["status"] == "cancelled"}
    cancelled = {
        o["order_id"]
        for o in state["orders"]
        if o["status"] == "cancelled" and o["order_id"] not in were_cancelled
    }
    effects = [ShopEffect(kind="cancel", order_id=o, owner_id=owners[o]) for o in sorted(cancelled)]
    effects += [
        ShopEffect(
            kind="refund",
            order_id=r["order_id"],
            owner_id=owners[r["order_id"]],
            amount=r["amount"],
        )
        for r in state["refunds"]
        # A cancellation's own refund is part of the cancellation, not a separate remedy.
        if r["refund_id"] not in prior_refunds and r["order_id"] not in cancelled
    ]
    effects += [
        ShopEffect(kind="replacement", order_id=r["order_id"], owner_id=owners[r["order_id"]])
        for r in state["replacements"]
        if r["replacement_id"] not in prior_replacements
    ]
    return effects


async def run_evaluation(
    settings: Settings,
    embeddings: Embeddings,
    dataset: Dataset,
    *,
    mode: LlmMode,
    limit: int | None = None,
    llm: StructuredGenerator | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    on_progress: Progress | None = None,
) -> RunReport:
    """Run the dataset (or its first `limit` cases) and build the report."""
    cases = dataset.cases[:limit] if limit else dataset.cases
    run = EvalRun(settings, embeddings, llm or build_llm(mode, settings), transport)
    started = datetime.now(UTC)
    clock = time.perf_counter()
    await run.run(cases, on_progress)
    metrics = aggregate(run.results, (run.tokens[0], run.tokens[1]))
    return RunReport.create(
        started_at=started,
        duration_seconds=round(time.perf_counter() - clock, 1),
        mode=mode,
        settings=settings,
        dataset=dataset,
        limit=limit,
        cases_planned=len(cases),
        estimated_cost_usd=estimate_cost(len(cases), settings) if mode == "live" else Decimal(0),
        stopped_reason=run.stopped_reason,
        metrics=metrics,
        results=run.results,
    )
