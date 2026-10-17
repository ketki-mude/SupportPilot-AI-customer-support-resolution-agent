"""Resolution: the model proposes one action, grounded in the retrieved policy sections."""

from typing import Any

from app.agents.llm import LLMError
from app.agents.nodes.base import step
from app.agents.prompts import resolution_messages
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState, EscalationCause
from app.schemas.agent import Citation, PolicySnippet, Proposal, ProposedAction, ResolutionOutput


@step("resolution")
async def resolution(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    try:
        output = await deps.llm.generate(ResolutionOutput, resolution_messages(state), deps.usage)
    except LLMError as exc:
        return {"escalation": EscalationCause.MODEL_FAILURE, "errors": [str(exc)]}
    proposal = Proposal(
        action=output.action,
        skus=output.skus,
        reason=output.reason,
        confidence=output.confidence,
        citations=grounded_citations(output.citations, state.policy_snippets),
        customer_reply=output.customer_reply if output.action is ProposedAction.INFO else None,
    )
    return {"proposal": proposal}


def grounded_citations(cited: list[Citation], snippets: list[PolicySnippet]) -> list[Citation]:
    """Keep only citations of sections that were actually retrieved, in their exact form.

    Models often cite "3.2" for "3.2 Damaged or defective items", so sections match on number.
    """
    by_number = {(s.source, s.section.split(" ", 1)[0]): s for s in snippets}
    grounded: dict[tuple[str, str], Citation] = {}
    for citation in cited:
        number = citation.section.strip().split(" ", 1)[0]
        snippet = by_number.get((citation.source.strip(), number))
        if snippet is not None:
            grounded[(snippet.source, snippet.section)] = Citation(
                source=snippet.source, section=snippet.section
            )
    return list(grounded.values())
