"""Executor: carry out the decided action, and only a decided one."""

import uuid
from typing import Any

from app.agents.guardrails import UnauthorizedActionError, authorize_action
from app.agents.nodes.base import step
from app.agents.runtime import AgentDeps
from app.agents.state import ActionResult, AgentState, EscalationCause
from app.agents.tools.commerce import CommerceError
from app.schemas.agent import PolicyDecision, ProposedAction
from app.schemas.commerce import Cancellation, Refund, Replacement
from app.services import approval_service
from app.services.audit_service import AuditStatus


@step("executor")
async def executor(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    ticket_id = uuid.UUID(state.ticket_id)
    approval = (
        await approval_service.get_approval(deps.session, uuid.UUID(state.approval.approval_id))
        if state.approval
        else None
    )
    try:
        authorization = authorize_action(state.decision, approval)
    except UnauthorizedActionError as exc:
        await deps.audit.record(
            ticket_id=ticket_id,
            action="action_blocked",
            status=AuditStatus.FAILURE,
            input_data={"decision": state.decision},
            output_data={"reason": str(exc)},
        )
        return {"escalation": EscalationCause.UNAUTHORIZED_ACTION, "errors": [str(exc)]}

    try:
        result = await _run(state, deps, authorization.decision)
    except CommerceError as exc:
        return {"escalation": EscalationCause.TOOL_FAILURE, "errors": [str(exc)]}
    return {"result": result}


async def _run(state: AgentState, deps: AgentDeps, decision: PolicyDecision) -> ActionResult:
    """Call the tool for the action. Tool idempotency keys are per ticket, so a repeat is safe."""
    if decision.action is ProposedAction.INFO:
        return ActionResult(action=ProposedAction.INFO)
    if state.order is None:
        raise RuntimeError("An order action needs an order")
    order_id = state.order.order_id
    reason = (state.proposal.reason if state.proposal else decision.reason)[:500]
    tools = deps.tools
    match decision.action:
        case ProposedAction.REFUND:
            refund: Refund = await tools["create_refund"].ainvoke(
                {"order_id": order_id, "amount": f"{decision.amount:.2f}", "reason": reason}
            )
            return ActionResult(
                action=decision.action, reference=refund.refund_id, amount=refund.amount
            )
        case ProposedAction.REPLACEMENT:
            replacement: Replacement = await tools["create_replacement"].ainvoke(
                {"order_id": order_id, "skus": decision.skus, "reason": reason}
            )
            return ActionResult(
                action=decision.action,
                reference=replacement.replacement_id,
                amount=decision.amount,
            )
        case ProposedAction.CANCEL:
            cancellation: Cancellation = await tools["cancel_order"].ainvoke(
                {"order_id": order_id, "reason": reason}
            )
            return ActionResult(
                action=decision.action,
                reference=order_id,
                amount=cancellation.refund.amount if cancellation.refund else None,
                payment_action=cancellation.payment_action,
            )
    raise UnauthorizedActionError(f"{decision.action.value} cannot be executed")
