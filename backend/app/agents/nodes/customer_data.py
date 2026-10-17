"""Customer data: fetch the customer and the order through the commerce API."""

from typing import Any

from app.agents.nodes.base import step
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState, Clarification, EscalationCause
from app.agents.tools.commerce import CommerceError, CommerceNotFoundError


@step("customer_data")
async def customer_data(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Ownership is checked by the policy gate, which records any mismatch in the audit log."""
    try:
        customer = await deps.tools["get_customer"].ainvoke({})
        if state.order_id is None:
            return {"customer": customer}
        try:
            order = await deps.tools["get_order"].ainvoke({"order_id": state.order_id})
        except CommerceNotFoundError:
            return {
                "customer": customer,
                "order_id": None,
                "clarification": Clarification.UNMATCHED,
            }
    except CommerceError as exc:
        return {"escalation": EscalationCause.TOOL_FAILURE, "errors": [str(exc)]}
    return {"customer": customer, "order": order}
