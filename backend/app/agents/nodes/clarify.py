"""Clarify: ask which order the customer means. The ticket stays open for their answer."""

from typing import Any

from app.agents import replies
from app.agents.nodes.base import say, step
from app.agents.runtime import AgentDeps
from app.agents.state import AgentState, Clarification, EscalationCause


@step("clarify")
async def clarify(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """After the configured number of questions, stop asking and hand over to a human."""
    if state.clarification_turns >= deps.settings.max_clarification_turns:
        return {"escalation": EscalationCause.CLARIFICATION_LIMIT}
    await say(
        deps,
        replies.clarification(state.clarification or Clarification.UNMATCHED, state.candidates),
    )
    return {"clarification_turns": state.clarification_turns + 1}
