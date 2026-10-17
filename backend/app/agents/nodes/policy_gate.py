"""Policy gate: the deterministic business rules decide, and every decision is audited."""

import uuid
from typing import Any

from app.agents.nodes.base import step
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState
from app.db.models import TicketCategory
from app.schemas.agent import Proposal, ProposedAction
from app.services.policy_engine import PolicyContext, evaluate

_ACTION_FOR_INTENT = {
    TicketCategory.REFUND: ProposedAction.REFUND,
    TicketCategory.REPLACEMENT: ProposedAction.REPLACEMENT,
    TicketCategory.CANCELLATION: ProposedAction.CANCEL,
}


@step("policy_gate")
async def policy_gate(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    if state.customer is None:
        raise RuntimeError("The policy gate needs the customer record")
    proposal = state.proposal or rules_only_proposal(state)
    context = PolicyContext(
        requester_customer_id=state.customer_id,
        intent=state.intent or TicketCategory.OTHER,
        customer=state.customer,
        order=state.order,
    )
    decision = evaluate(proposal, context, deps.settings)
    await deps.audit.record(
        ticket_id=uuid.UUID(state.ticket_id),
        action="policy_decision",
        status=decision.decision,
        input_data={
            "intent": context.intent,
            "order_id": state.order.order_id if state.order else None,
            "proposal": proposal.model_dump(exclude={"customer_reply"}),
        },
        output_data=decision,
    )
    return {"proposal": proposal, "decision": decision}


def rules_only_proposal(state: AgentState) -> Proposal:
    """Used when the model is deliberately skipped, for example when the order belongs to
    someone else: its details must never reach the model, and the rules decide alone."""
    action = _ACTION_FOR_INTENT.get(state.intent or TicketCategory.OTHER, ProposedAction.INFO)
    return Proposal(action=action, reason="Decided by the business rules alone.", confidence=1.0)
