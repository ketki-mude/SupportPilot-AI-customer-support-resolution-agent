"""Policy RAG: retrieve the policy sections relevant to this request."""

from typing import Any

from app.agents.nodes.base import step
from app.agents.nodes.intake import latest_customer_text
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState
from app.db.models import TicketCategory
from app.rag.retriever import retrieve_policies

# The documents that govern each intent.
POLICY_SOURCES: dict[TicketCategory, list[str]] = {
    TicketCategory.REFUND: ["refund_policy.md"],
    TicketCategory.REPLACEMENT: ["refund_policy.md"],
    TicketCategory.CANCELLATION: ["cancellation_policy.md"],
    TicketCategory.MISSING_ORDER: ["shipping_policy.md"],
    TicketCategory.BILLING: ["billing_policy.md"],
}


@step("policy_rag")
async def policy_rag(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Search the documents that govern the classified intent.

    Customers' words often score low against policy text ("my kettle leaks" against "Damaged or
    defective items"), so a similarity floor on its own drops the very sections that apply, and
    the model then has nothing to ground a proposal in. Within the governing documents the best
    matches are relevant by construction, so no floor is applied there; it still guards searches
    with no known intent.
    """
    topic = state.intent.value.replace("_", " ") if state.intent else "support"
    query = f"{topic}: {latest_customer_text(state.messages)}"
    sources = POLICY_SOURCES.get(state.intent) if state.intent else None
    snippets = await retrieve_policies(
        deps.session,
        deps.embeddings,
        query,
        top_k=deps.settings.rag_top_k,
        min_similarity=0.0 if sources else deps.settings.rag_min_similarity,
        sources=sources,
    )
    return {"policy_snippets": snippets}
