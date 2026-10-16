"""The migrated schema: drift check, generated ids, and integrity constraints."""

import re
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Role
from app.db.models import (
    EMBEDDING_DIMENSIONS,
    Approval,
    ApprovalStatus,
    AuditLog,
    PolicyChunk,
    Ticket,
    TicketStatus,
    User,
)
from tests.conftest import alembic_config


def test_models_match_migrations(database_url: str) -> None:
    """Fails if a model changed without a matching Alembic migration."""
    command.check(alembic_config(database_url))


async def test_pgvector_extension_enabled(db_session: AsyncSession) -> None:
    installed = await db_session.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    assert installed is not None


async def _ticket(session: AsyncSession) -> Ticket:
    ticket = Ticket(customer_id="cus_001", order_id="ORD-10001")
    session.add(ticket)
    await session.flush()
    return ticket


async def test_ticket_gets_sequential_public_id(db_session: AsyncSession) -> None:
    first = await _ticket(db_session)
    second = await _ticket(db_session)
    assert re.fullmatch(r"TCK-\d{4,}", first.public_id)
    assert int(second.public_id[4:]) == int(first.public_id[4:]) + 1
    assert first.status is TicketStatus.OPEN


async def test_ticket_update_returns_updated_at_without_lazy_load(
    db_session: AsyncSession,
) -> None:
    ticket = await _ticket(db_session)
    ticket.status = TicketStatus.RESOLVED
    await db_session.flush()
    # Fetched via RETURNING; a lazy refresh here would fail under asyncio.
    assert "updated_at" not in inspect(ticket).expired_attributes
    assert ticket.updated_at is not None


async def test_audit_log_is_append_only(db_session: AsyncSession) -> None:
    entry = AuditLog(actor="agent", action="tool_call", tool_name="get_order", status="success")
    db_session.add(entry)
    await db_session.flush()
    assert entry.input == {}

    for statement in (
        update(AuditLog).where(AuditLog.id == entry.id).values(status="tampered"),
        delete(AuditLog).where(AuditLog.id == entry.id),
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db_session.begin_nested():
                await db_session.execute(statement)

    stored = await db_session.scalar(select(AuditLog.status).where(AuditLog.id == entry.id))
    assert stored == "success"


async def test_only_one_pending_approval_per_ticket(db_session: AsyncSession) -> None:
    ticket = await _ticket(db_session)
    db_session.add(Approval(ticket_id=ticket.id, proposed_action={"action": "refund"}))
    await db_session.flush()
    with pytest.raises(IntegrityError, match="uq_approvals_one_pending_per_ticket"):
        async with db_session.begin_nested():
            db_session.add(Approval(ticket_id=ticket.id, proposed_action={"action": "refund"}))
            await db_session.flush()


async def test_decided_approval_must_record_who_and_when(
    db_session: AsyncSession, users: dict[Role, User]
) -> None:
    ticket = await _ticket(db_session)
    with pytest.raises(IntegrityError, match="decision_recorded"):
        async with db_session.begin_nested():
            db_session.add(
                Approval(
                    ticket_id=ticket.id,
                    proposed_action={"action": "refund"},
                    status=ApprovalStatus.APPROVED,
                )
            )
            await db_session.flush()

    approved = Approval(
        ticket_id=ticket.id,
        proposed_action={"action": "refund"},
        status=ApprovalStatus.APPROVED,
        decided_by=users[Role.SUPERVISOR].id,
        decided_at=datetime.now(UTC),
    )
    db_session.add(approved)
    await db_session.flush()


@pytest.mark.parametrize(("role", "customer_id"), [(Role.CUSTOMER, None), (Role.AGENT, "cus_001")])
async def test_customer_link_matches_role(
    db_session: AsyncSession, role: Role, customer_id: str | None
) -> None:
    with pytest.raises(IntegrityError, match="customer_link"):
        async with db_session.begin_nested():
            db_session.add(
                User(email="x@acme.com", password_hash="x", role=role, customer_id=customer_id)
            )
            await db_session.flush()


async def test_enum_columns_reject_unknown_values(db_session: AsyncSession) -> None:
    ticket = await _ticket(db_session)
    with pytest.raises(IntegrityError, match="ticket_status"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE tickets SET status = 'deleted' WHERE id = :id"), {"id": ticket.id}
            )


async def test_policy_chunk_vector_search(db_session: AsyncSession) -> None:
    near = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
    far = [0.0] * (EMBEDDING_DIMENSIONS - 1) + [1.0]
    db_session.add_all(
        [
            PolicyChunk(source="refund_policy.md", section="3.2", content="near", embedding=near),
            PolicyChunk(source="refund_policy.md", section="9.9", content="far", embedding=far),
        ]
    )
    await db_session.flush()
    closest = await db_session.scalar(
        select(PolicyChunk.content).order_by(PolicyChunk.embedding.cosine_distance(near)).limit(1)
    )
    assert closest == "near"
