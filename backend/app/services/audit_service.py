"""Append-only audit trail. Entries are only ever inserted; the database rejects edits."""

import uuid
from enum import StrEnum
from typing import Any

from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLog

AGENT_ACTOR = "agent"
# Contact details stay out of the audit trail; ids and order data are enough to reconstruct events.
_PII_KEYS = frozenset({"email", "phone"})


class AuditStatus(StrEnum):
    """Outcome labels. Policy and approval entries use their own decision values instead."""

    SUCCESS = "success"
    FAILURE = "failure"
    FLAGGED = "flagged"
    ESCALATED = "escalated"
    REJECTED_INVALID = "rejected_invalid"


class AuditService:
    """Writes each entry in its own short transaction.

    A failed tool call is therefore recorded even if the caller's transaction rolls back. Any
    ticket an entry refers to must already be committed so its foreign key is visible.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        *,
        action: str,
        status: StrEnum,
        actor: str = AGENT_ACTOR,
        ticket_id: uuid.UUID | None = None,
        tool_name: str | None = None,
        input_data: Any = None,
        output_data: Any = None,
    ) -> AuditLog:
        """Append one entry and commit it immediately."""
        entry = AuditLog(
            ticket_id=ticket_id,
            actor=actor,
            action=action,
            tool_name=tool_name,
            input=sanitize(input_data),
            output=sanitize(output_data),
            status=status.value,
        )
        async with self._session_factory() as session:
            session.add(entry)
            await session.commit()
        return entry


def sanitize(value: Any) -> dict[str, Any]:
    """Turn models, decimals, and dates into JSON, dropping contact details at any depth."""
    if value is None:
        return {}
    data = to_jsonable_python(value)
    if not isinstance(data, dict):
        data = {"value": data}
    return {key: _drop_pii(item) for key, item in data.items() if key not in _PII_KEYS}


def _drop_pii(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_pii(item) for key, item in value.items() if key not in _PII_KEYS}
    if isinstance(value, list):
        return [_drop_pii(item) for item in value]
    return value
