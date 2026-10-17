"""Escalation: hand the ticket to a human with no automated action."""

import uuid
from typing import Any

from app.agents import replies
from app.agents.nodes.base import say, set_ticket, step
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState, EscalationCause
from app.db.models import TicketPriority, TicketStatus
from app.services.audit_service import AuditStatus


@step("escalation")
async def escalation(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Supervisors are notified through the console, where escalated tickets are queued first."""
    cause = state.escalation or EscalationCause.POLICY
    await set_ticket(deps, status=TicketStatus.ESCALATED, priority=TicketPriority.HIGH)
    await deps.session.commit()
    decision = state.decision
    await deps.audit.record(
        ticket_id=uuid.UUID(state.ticket_id),
        action="escalated",
        status=AuditStatus.ESCALATED,
        output_data={
            "cause": cause,
            "rule": decision.rule if decision else None,
            "reason": decision.reason if decision else "; ".join(state.errors) or None,
            "injection_flags": state.injection_flags,
        },
    )
    await say(deps, replies.escalated(state))
    return {"escalation": cause}
