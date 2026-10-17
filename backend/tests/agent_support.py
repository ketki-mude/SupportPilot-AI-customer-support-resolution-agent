"""Helpers for agent tests: a scripted model, a real-commit database, and an agent runner."""

import uuid
from collections import deque
from collections.abc import Sequence
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime import AgentEvent, EventType
from app.db.models import AgentRun, AuditLog, Message, MessageSender, Ticket
from app.observability.cost import UsageTracker
from app.schemas.agent import Citation, IntakeOutput, ProposedAction, ResolutionOutput
from app.services import ticket_service

SchemaT = TypeVar("SchemaT", bound=BaseModel)

SCRIPTED_INPUT_TOKENS = 100
SCRIPTED_OUTPUT_TOKENS = 20


class ScriptedLLM:
    """Plays back queued answers per schema and records every call it receives."""

    def __init__(self) -> None:
        self._answers: dict[type[BaseModel], deque[BaseModel | Exception]] = {}
        self.calls: list[tuple[type[BaseModel], list[BaseMessage]]] = []

    def queue(self, *answers: BaseModel | Exception, schema: type[BaseModel] | None = None) -> None:
        for answer in answers:
            key = schema or type(answer)
            self._answers.setdefault(key, deque()).append(answer)

    async def generate(
        self, schema: type[SchemaT], messages: list[BaseMessage], usage: UsageTracker
    ) -> SchemaT:
        self.calls.append((schema, messages))
        pending = self._answers.get(schema)
        if not pending:
            raise AssertionError(f"Unexpected model call for {schema.__name__}")
        answer = pending.popleft()
        usage.add({"input_tokens": SCRIPTED_INPUT_TOKENS, "output_tokens": SCRIPTED_OUTPUT_TOKENS})
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, schema)
        return answer

    def schemas_called(self) -> list[str]:
        return [schema.__name__ for schema, _ in self.calls]


def intake(
    intent: str = "refund",
    orders: Sequence[str] = ("ORD-10001",),
    confidence: float = 0.95,
) -> IntakeOutput:
    return IntakeOutput.model_validate(
        {
            "intent": intent,
            "matching_order_ids": list(orders),
            "confidence": confidence,
            "summary": "Customer reports a problem with an order.",
        }
    )


def resolution(
    action: ProposedAction | str = "refund",
    *,
    confidence: float = 0.9,
    skus: Sequence[str] = (),
    citations: Sequence[tuple[str, str]] = (("refund_policy.md", "3.2"),),
    reply: str | None = None,
) -> ResolutionOutput:
    return ResolutionOutput(
        action=ProposedAction(action),
        skus=list(skus),
        reason="Proposed from the order facts and the refund policy.",
        confidence=confidence,
        citations=[Citation(source=src, section=sec) for src, sec in citations],
        customer_reply=reply,
    )


async def open_ticket(
    sessions: async_sessionmaker[AsyncSession], message: str, customer_id: str = "cus_001"
) -> uuid.UUID:
    async with sessions() as session:
        ticket = await ticket_service.create_ticket(
            session, customer_id=customer_id, first_message=message
        )
        await session.commit()
        return ticket.id


async def load_ticket(sessions: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID) -> Ticket:
    async with sessions() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket is not None
        return ticket


async def agent_messages(
    sessions: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID
) -> list[str]:
    async with sessions() as session:
        rows = await session.scalars(
            select(Message.content)
            .where(Message.ticket_id == ticket_id, Message.sender == MessageSender.AGENT)
            .order_by(Message.created_at)
        )
        return list(rows)


async def audit_trail(
    sessions: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID, action: str | None = None
) -> list[AuditLog]:
    async with sessions() as session:
        query = select(AuditLog).where(AuditLog.ticket_id == ticket_id)
        if action:
            query = query.where(AuditLog.action == action)
        return list(await session.scalars(query.order_by(AuditLog.created_at)))


async def runs_for(
    sessions: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID
) -> list[AgentRun]:
    async with sessions() as session:
        rows = await session.scalars(
            select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.created_at)
        )
        return list(rows)


async def count(sessions: async_sessionmaker[AsyncSession], model: type[Any]) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


def step_trace(events: list[AgentEvent]) -> list[str]:
    """Readable event sequence: 'start:intake', 'end:intake', 'message', 'approval_requested'."""
    labels = []
    for event in events:
        if event.type is EventType.STEP_STARTED:
            labels.append(f"start:{event.data['step']}")
        elif event.type is EventType.STEP_FINISHED:
            labels.append(f"end:{event.data['step']}")
        else:
            labels.append(event.type.value)
    return labels
