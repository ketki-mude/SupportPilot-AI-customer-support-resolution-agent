"""Policy RAG: retrieve the policy sections relevant to this request."""

from typing import Any

from app.agents.nodes.base import step
from app.agents.nodes.intake import latest_customer_text
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState
from app.rag.retriever import retrieve_policies


@step("policy_rag")
async def policy_rag(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    topic = state.intent.value.replace("_", " ") if state.intent else "support"
    query = f"{topic}: {latest_customer_text(state.messages)}"
    snippets = await retrieve_policies(
        deps.session,
        deps.embeddings,
        query,
        top_k=deps.settings.rag_top_k,
        min_similarity=deps.settings.rag_min_similarity,
    )
    return {"policy_snippets": snippets}
