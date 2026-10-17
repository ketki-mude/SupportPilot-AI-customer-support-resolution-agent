"""Runs the agent graph in the background for a ticket and records each run.

One run at a time per ticket. Each run gets its own database session, a usage tracker for
tokens and cost, and an agent_runs row with its latency and final status.
"""

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any

import structlog
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import replies
from app.agents.llm import StructuredGenerator
from app.agents.runtime import AgentDeps, EventBus, EventType, TicketEvents
from app.agents.state import AgentState
from app.agents.tools import build_tools
from app.agents.tools.base import ToolContext
from app.agents.tools.commerce import CommerceClient
from app.core.config import Settings
from app.db.models import AgentRun, AgentRunStatus, MessageSender, TicketStatus
from app.observability.cost import UsageTracker
from app.services import ticket_service
from app.services.audit_service import AuditService, AuditStatus

logger = structlog.get_logger(__name__)

AgentGraph = CompiledStateGraph[AgentState, AgentDeps, AgentState, AgentState]
_ESCALATABLE = frozenset({TicketStatus.OPEN, TicketStatus.AWAITING_APPROVAL})


class AgentRunner:
    def __init__(
        self,
        *,
        graph: AgentGraph,
        session_factory: async_sessionmaker[AsyncSession],
        commerce: CommerceClient,
        audit: AuditService,
        llm: StructuredGenerator,
        embeddings: Embeddings,
        settings: Settings,
        events: EventBus,
        tracer: BaseCallbackHandler | None = None,
    ) -> None:
        self.graph = graph
        self.commerce = commerce
        self.audit = audit
        self.events = events
        self._session_factory = session_factory
        self._llm = llm
        self._embeddings = embeddings
        self._settings = settings
        self._tracer = tracer
        self._locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task[AgentRunStatus | None]] = set()

    def start(self, ticket_id: uuid.UUID, customer_id: str) -> asyncio.Task[AgentRunStatus | None]:
        """Process the ticket's latest customer message in the background."""
        graph_input = {"ticket_id": str(ticket_id), "customer_id": customer_id}
        return self._spawn(ticket_id, customer_id, graph_input)

    def resume(self, ticket_id: uuid.UUID, customer_id: str) -> asyncio.Task[AgentRunStatus | None]:
        """Continue a run paused for approval. The decision itself is read from the database.

        Resuming a ticket with nothing paused (a duplicate resume) does nothing.
        """
        return self._spawn(ticket_id, customer_id, Command(resume={"decided": True}))

    async def wait(self) -> None:
        """Wait for every background run to finish (tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _spawn(
        self, ticket_id: uuid.UUID, customer_id: str, graph_input: Any
    ) -> asyncio.Task[AgentRunStatus | None]:
        task = asyncio.create_task(self.run(ticket_id, customer_id, graph_input))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _config(self, ticket_id: uuid.UUID) -> RunnableConfig:
        return {
            "configurable": {"thread_id": str(ticket_id)},
            "run_name": "support_ticket",
            "tags": ["supportpilot"],
            "metadata": {"ticket_id": str(ticket_id)},
            "callbacks": [self._tracer] if self._tracer else [],
        }

    async def run(
        self, ticket_id: uuid.UUID, customer_id: str, graph_input: Any
    ) -> AgentRunStatus | None:
        """Run the graph to its next pause or end, then record the run.

        Returns None when there was nothing to do (a resume with no paused run).
        """
        with structlog.contextvars.bound_contextvars(ticket_id=str(ticket_id)):
            async with self._locks[ticket_id]:
                if isinstance(graph_input, Command) and not await self._is_paused(ticket_id):
                    logger.info("resume_skipped", reason="nothing is waiting for approval")
                    return None
                return await self._run_locked(ticket_id, customer_id, graph_input)

    async def _is_paused(self, ticket_id: uuid.UUID) -> bool:
        snapshot = await self.graph.aget_state(self._config(ticket_id))
        return "await_approval" in snapshot.next

    async def _run_locked(
        self, ticket_id: uuid.UUID, customer_id: str, graph_input: Any
    ) -> AgentRunStatus:
        async with self._session_factory() as session:
            run = AgentRun(ticket_id=ticket_id, thread_id=str(ticket_id))
            session.add(run)
            await session.commit()

            events = TicketEvents(self.events, str(ticket_id))
            deps = AgentDeps(
                session=session,
                tools=build_tools(
                    ToolContext(
                        ticket_id=ticket_id,
                        customer_id=customer_id,
                        session=session,
                        commerce=self.commerce,
                        audit=self.audit,
                    )
                ),
                audit=self.audit,
                llm=self._llm,
                embeddings=self._embeddings,
                settings=self._settings,
                events=events,
                usage=UsageTracker(),
            )
            started = time.perf_counter()
            status = AgentRunStatus.FAILED
            try:
                # A run that hangs (a stuck dependency) is cut off and handed to a human.
                async with asyncio.timeout(self._settings.agent_run_timeout_seconds):
                    result = await self.graph.ainvoke(
                        graph_input, self._config(ticket_id), context=deps
                    )
                status = (
                    AgentRunStatus.PAUSED
                    if result.get("__interrupt__")
                    else AgentRunStatus.COMPLETED
                )
            except Exception as exc:
                logger.exception("agent_run_failed", ticket_id=str(ticket_id))
                await session.rollback()
                await self._fail_safe(session, ticket_id, exc, events)
            finally:
                await self._record(session, run, ticket_id, status, deps.usage, started)

            if status is AgentRunStatus.COMPLETED:
                ticket = await ticket_service.get_ticket(session, ticket_id)
                events.publish(EventType.COMPLETED, ticket_status=ticket.status.value)
            return status

    async def _record(
        self,
        session: AsyncSession,
        run: AgentRun,
        ticket_id: uuid.UUID,
        status: AgentRunStatus,
        usage: UsageTracker,
        started: float,
    ) -> None:
        run.status = status
        run.input_tokens = usage.input_tokens
        run.output_tokens = usage.output_tokens
        run.cost_usd = usage.cost(self._settings)
        run.latency_ms = round((time.perf_counter() - started) * 1000)
        session.add(run)
        await session.commit()
        logger.info(
            "agent_run_recorded",
            ticket_id=str(ticket_id),
            status=status.value,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=str(usage.cost(self._settings)),
        )

    async def _fail_safe(
        self, session: AsyncSession, ticket_id: uuid.UUID, error: Exception, events: TicketEvents
    ) -> None:
        """An unexpected failure must never leave the customer waiting silently."""
        await self.audit.record(
            ticket_id=ticket_id,
            action="run_failed",
            status=AuditStatus.FAILURE,
            output_data={"error": type(error).__name__, "message": str(error)},
        )
        ticket = await ticket_service.get_ticket(session, ticket_id)
        if ticket.status in _ESCALATABLE:
            ticket.status = TicketStatus.ESCALATED
            message = await ticket_service.add_message(
                session, ticket_id, MessageSender.AGENT, replies.RUN_FAILED
            )
            await session.commit()
            events.publish(
                EventType.MESSAGE,
                message_id=str(message.id),
                sender=message.sender.value,
                content=message.content,
            )
        events.publish(EventType.ERROR, message="The agent hit an unexpected error")
