"""What every node can use during a run, and the live event stream clients subscribe to."""

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.tools import StructuredTool
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import StructuredGenerator
from app.core.config import Settings
from app.observability.cost import UsageTracker
from app.services.audit_service import AuditService

HISTORY_PER_TICKET = 200
HEARTBEAT_SECONDS = 15.0


class EventType(StrEnum):
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    MESSAGE = "message"
    APPROVAL_REQUESTED = "approval_requested"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    id: int
    type: EventType
    data: dict[str, Any]

    def encode(self) -> str:
        """Server-Sent Events wire format."""
        payload = json.dumps(to_jsonable_python(self.data), separators=(",", ":"))
        return f"id: {self.id}\nevent: {self.type.value}\ndata: {payload}\n\n"


class EventBus:
    """In-process pub/sub of agent events per ticket, with a short replay history.

    Late subscribers first get the recent history, so nothing is missed between starting a run
    and opening the stream. Single-process only; several API workers would need a shared broker.
    """

    def __init__(self) -> None:
        self._history: dict[str, deque[AgentEvent]] = defaultdict(
            lambda: deque(maxlen=HISTORY_PER_TICKET)
        )
        self._subscribers: dict[str, set[asyncio.Queue[AgentEvent]]] = defaultdict(set)
        self._last_id: dict[str, int] = defaultdict(int)

    def publish(self, ticket_id: str, event_type: EventType, data: dict[str, Any]) -> AgentEvent:
        self._last_id[ticket_id] += 1
        event = AgentEvent(self._last_id[ticket_id], event_type, data)
        self._history[ticket_id].append(event)
        for queue in self._subscribers[ticket_id]:
            queue.put_nowait(event)
        return event

    def history(self, ticket_id: str) -> list[AgentEvent]:
        return list(self._history[ticket_id])

    async def subscribe(
        self, ticket_id: str, *, after: int = 0, heartbeat: float = HEARTBEAT_SECONDS
    ) -> AsyncIterator[AgentEvent | None]:
        """Yield events newer than `after`, then live ones. None means "send a keep-alive"."""
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._subscribers[ticket_id].add(queue)
        try:
            last = after
            for event in self.history(ticket_id):
                if event.id > last:
                    last = event.id
                    yield event
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield None
                    continue
                if event.id > last:
                    last = event.id
                    yield event
        finally:
            self._subscribers[ticket_id].discard(queue)


@dataclass(frozen=True, slots=True)
class TicketEvents:
    """The event bus scoped to one ticket."""

    bus: EventBus
    ticket_id: str

    def publish(self, event_type: EventType, **data: Any) -> AgentEvent:
        return self.bus.publish(self.ticket_id, event_type, data)


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """The LangGraph runtime context for one run of one ticket."""

    session: AsyncSession
    tools: dict[str, StructuredTool]
    audit: AuditService
    llm: StructuredGenerator
    embeddings: Embeddings
    settings: Settings
    events: TicketEvents
    usage: UsageTracker = field(default_factory=UsageTracker)
