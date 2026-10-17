"""The LangGraph workflow. Every route below reads facts from state; none asks the model.

intake -> customer_data -> policy_rag -> resolution -> policy_gate
policy_gate -> executor | request_approval -> await_approval | notifier | escalation
clarify and escalation can be reached from any step that needs a human or more detail.
"""

from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.approval_gate import await_approval, request_approval
from app.agents.nodes.clarify import clarify
from app.agents.nodes.customer_data import customer_data
from app.agents.nodes.escalation import escalation
from app.agents.nodes.executor import executor
from app.agents.nodes.intake import intake
from app.agents.nodes.notifier import notifier
from app.agents.nodes.policy_gate import policy_gate
from app.agents.nodes.policy_rag import policy_rag
from app.agents.nodes.resolution import resolution
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState
from app.db.models import ApprovalStatus, TicketCategory
from app.schemas.agent import Decision


def build_graph() -> StateGraph[AgentState, AgentDeps, AgentState, AgentState]:
    graph = StateGraph(AgentState, context_schema=AgentDeps)
    for name, node in (
        ("intake", intake),
        ("clarify", clarify),
        ("customer_data", customer_data),
        ("policy_rag", policy_rag),
        ("resolution", resolution),
        ("policy_gate", policy_gate),
        ("request_approval", request_approval),
        ("await_approval", await_approval),
        ("executor", executor),
        ("notifier", notifier),
        ("escalation", escalation),
    ):
        graph.add_node(name, node)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", after_intake)
    graph.add_conditional_edges("clarify", after_clarify)
    graph.add_conditional_edges("customer_data", after_customer_data)
    graph.add_edge("policy_rag", "resolution")
    graph.add_conditional_edges("resolution", after_resolution)
    graph.add_conditional_edges("policy_gate", after_policy_gate)
    graph.add_edge("request_approval", "await_approval")
    graph.add_conditional_edges("await_approval", after_approval)
    graph.add_conditional_edges("executor", after_executor)
    graph.add_edge("notifier", END)
    graph.add_edge("escalation", END)
    return graph


def after_intake(state: AgentState) -> Literal["escalation", "clarify", "customer_data"]:
    if state.escalation:
        return "escalation"
    return "clarify" if state.clarification else "customer_data"


def after_clarify(state: AgentState) -> str:
    return "escalation" if state.escalation else END


def after_customer_data(
    state: AgentState,
) -> Literal["escalation", "clarify", "policy_gate", "policy_rag"]:
    if state.escalation:
        return "escalation"
    if state.clarification:
        return "clarify"
    # Someone else's order, or an unknown intent: the rules decide without the model.
    if not state.owns_order or state.intent is TicketCategory.OTHER:
        return "policy_gate"
    return "policy_rag"


def after_resolution(state: AgentState) -> Literal["escalation", "policy_gate"]:
    return "escalation" if state.escalation else "policy_gate"


def after_policy_gate(
    state: AgentState,
) -> Literal["executor", "request_approval", "notifier", "escalation"]:
    match state.decision.decision if state.decision else Decision.ESCALATE:
        case Decision.AUTO_APPROVE:
            return "executor"
        case Decision.NEEDS_APPROVAL:
            return "request_approval"
        case Decision.DENY:
            return "notifier"
        case _:
            return "escalation"


def after_approval(state: AgentState) -> Literal["executor", "notifier"]:
    approved = state.approval is not None and state.approval.status is ApprovalStatus.APPROVED
    return "executor" if approved else "notifier"


def after_executor(state: AgentState) -> Literal["escalation", "notifier"]:
    return "escalation" if state.escalation else "notifier"
