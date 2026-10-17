"""Notifier: tell the customer the outcome and close the ticket."""

from typing import Any

from app.agents import replies
from app.agents.nodes.base import say, set_ticket, step
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState
from app.db.models import ApprovalStatus, TicketStatus
from app.schemas.agent import Decision


@step("notifier")
async def notifier(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    denied = state.decision is not None and state.decision.decision is Decision.DENY
    rejected = state.approval is not None and state.approval.status is ApprovalStatus.REJECTED
    await say(deps, replies.outcome(state))
    await set_ticket(
        deps, status=TicketStatus.REJECTED if denied or rejected else TicketStatus.RESOLVED
    )
    return {}
