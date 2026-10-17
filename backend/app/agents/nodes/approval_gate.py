"""Approval gate: open a supervisor approval, then pause the run until it is decided.

Two nodes, because LangGraph re-runs an interrupted node from the top when it resumes. The side
effects (approval row, ticket status, customer message) live in `request_approval`, which is
checkpointed before `await_approval` pauses, so resuming never repeats them.
"""

import uuid
from typing import Any

from langgraph.types import interrupt

from app.agents import replies
from app.agents.nodes.base import say, set_ticket, step
from app.agents.runtime import AgentDeps, EventType
from app.agents.state import AgentState, ApprovalOutcome
from app.db.models import ApprovalStatus, TicketStatus
from app.services import approval_service


@step("request_approval")
async def request_approval(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    decision, order, proposal = state.decision, state.order, state.proposal
    if decision is None or order is None or proposal is None:
        raise RuntimeError("An approval needs a decision, a proposal, and an order")
    ticket_id = uuid.UUID(state.ticket_id)
    proposed_action = {
        "action": decision.action.value,
        "amount": None if decision.amount is None else f"{decision.amount:.2f}",
        "skus": decision.skus,
        "order_id": order.order_id,
        "rule": decision.rule.value,
        "policy_reason": decision.reason,
        "agent_reason": proposal.reason,
        "confidence": proposal.confidence,
        "citations": [c.model_dump() for c in proposal.citations],
    }
    approval = await approval_service.open_approval(deps.session, ticket_id, proposed_action)
    await set_ticket(deps, status=TicketStatus.AWAITING_APPROVAL)
    await deps.session.commit()
    await deps.audit.record(
        ticket_id=ticket_id,
        action="approval_requested",
        status=ApprovalStatus.PENDING,
        output_data={"approval_id": str(approval.id), **proposed_action},
    )
    await say(deps, replies.approval_requested(decision, order))
    deps.events.publish(
        EventType.APPROVAL_REQUESTED,
        approval_id=str(approval.id),
        action=decision.action.value,
        amount=proposed_action["amount"],
    )
    return {
        "approval": ApprovalOutcome(approval_id=str(approval.id), status=ApprovalStatus.PENDING)
    }


@step("await_approval")
async def await_approval(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Pause here. On resume, trust the approval row in the database, not the resume payload."""
    if state.approval is None:
        raise RuntimeError("No approval to wait for")
    interrupt({"approval_id": state.approval.approval_id})
    approval = await approval_service.get_approval(
        deps.session, uuid.UUID(state.approval.approval_id)
    )
    if approval.status is ApprovalStatus.PENDING:
        raise RuntimeError("The run resumed before a supervisor decided")
    return {"approval": ApprovalOutcome(approval_id=str(approval.id), status=approval.status)}
