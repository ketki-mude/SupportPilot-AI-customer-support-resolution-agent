"""Audit service: sanitising payloads and committing independently of the caller."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import AuditLog, Ticket
from app.schemas.ticket import TicketUpdate
from app.services import ticket_service
from app.services.audit_service import AuditService, AuditStatus, sanitize


def test_sanitize_serialises_and_drops_contact_details() -> None:
    payload = {
        "amount": Decimal("79.99"),
        "when": datetime(2026, 1, 2, tzinfo=UTC),
        "customer": {"name": "Jordan", "email": "j@example.com", "phone": "+1-555-0101"},
        "items": [{"sku": "A", "email": "x@example.com"}],
    }
    assert sanitize(payload) == {
        "amount": "79.99",
        "when": "2026-01-02T00:00:00Z",
        "customer": {"name": "Jordan"},
        "items": [{"sku": "A"}],
    }


def test_sanitize_wraps_scalars_and_handles_none() -> None:
    assert sanitize(None) == {}
    assert sanitize("ok") == {"value": "ok"}


async def test_record_persists_an_entry(audit: AuditService, db_session: AsyncSession) -> None:
    entry = await audit.record(
        action="policy_decision", status=AuditStatus.SUCCESS, input_data={"rule": "fraud_flag"}
    )
    stored = await db_session.get(AuditLog, entry.id)
    assert stored is not None
    assert (stored.actor, stored.action, stored.status) == ("agent", "policy_decision", "success")
    assert stored.ticket_id is None
    assert stored.input == {"rule": "fraud_flag"}


@pytest.fixture
async def committing_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Real, separately committed sessions; tables are emptied afterwards."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE audit_logs, messages, approvals, agent_runs, tickets CASCADE")
        )
    await engine.dispose()


async def test_failure_entry_survives_the_callers_rollback(
    committing_factory: async_sessionmaker[AsyncSession],
) -> None:
    audit = AuditService(committing_factory)
    async with committing_factory() as session:
        ticket = await ticket_service.create_ticket(
            session, customer_id="cus_001", first_message="Refund please"
        )
        await session.commit()
        ticket_id = ticket.id

        await ticket_service.update_ticket(session, ticket, TicketUpdate(summary="half-done work"))
        await audit.record(
            ticket_id=ticket_id,
            action="tool_call",
            tool_name="create_refund",
            status=AuditStatus.FAILURE,
            output_data={"code": "commerce_unavailable"},
        )
        await session.rollback()

    async with committing_factory() as session:
        entries = list(
            await session.scalars(select(AuditLog).where(AuditLog.ticket_id == ticket_id))
        )
        stored_ticket = await session.get(Ticket, ticket_id)
    assert [e.status for e in entries] == ["failure"]
    assert stored_ticket is not None
    assert stored_ticket.summary is None
