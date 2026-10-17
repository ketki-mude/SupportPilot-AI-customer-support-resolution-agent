"""The audit trail, newest first, optionally for one ticket."""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, StaffUser
from app.db.models import AuditLog, Ticket, User
from app.schemas.common import Page
from app.schemas.errors import ErrorResponse
from app.schemas.ticket import AuditEntryListItem, AuditEntryRead
from app.services.audit_service import AGENT_ACTOR
from app.services.ticket_service import TicketNotFoundError

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)

_PUBLIC_ID = re.compile(r"^TCK-\d+$", re.IGNORECASE)


@router.get("", responses={404: {"model": ErrorResponse}})
async def list_audit(
    _: StaffUser,
    session: SessionDep,
    ticket_id: Annotated[
        str | None, Query(max_length=64, description="Ticket id or number, e.g. TCK-1001")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AuditEntryListItem]:
    conditions = []
    if ticket_id:
        conditions.append(AuditLog.ticket_id == await _resolve_ticket(session, ticket_id.strip()))
    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
    rows = (
        await session.execute(
            select(AuditLog, Ticket.public_id)
            .outerjoin(Ticket, Ticket.id == AuditLog.ticket_id)
            .where(*conditions)
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    labels = await _actor_labels(session, {entry.actor for entry, _ in rows})
    items = [
        AuditEntryListItem(
            **AuditEntryRead.model_validate(entry).model_dump(),
            ticket_public_id=public_id,
            actor_label=labels.get(entry.actor, entry.actor),
        )
        for entry, public_id in rows
    ]
    return Page(items=items, total=total or 0, page=page, page_size=page_size)


async def _resolve_ticket(session: AsyncSession, value: str) -> uuid.UUID:
    """Accept either the ticket's UUID or its public number."""
    if _PUBLIC_ID.match(value):
        found = await session.scalar(select(Ticket.id).where(Ticket.public_id == value.upper()))
    else:
        try:
            found = await session.scalar(select(Ticket.id).where(Ticket.id == uuid.UUID(value)))
        except ValueError:
            found = None
    if found is None:
        raise TicketNotFoundError("Ticket not found")
    return found


async def _actor_labels(session: AsyncSession, actors: set[str]) -> dict[str, str]:
    """Show people by email and the agent as "agent"."""
    ids = []
    for actor in actors - {AGENT_ACTOR}:
        try:
            ids.append(uuid.UUID(actor))
        except ValueError:
            continue
    labels = {AGENT_ACTOR: AGENT_ACTOR}
    if ids:
        for user_id, email in await session.execute(
            select(User.id, User.email).where(User.id.in_(ids))
        ):
            labels[str(user_id)] = email
    return labels
