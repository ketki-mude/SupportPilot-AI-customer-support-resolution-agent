"""A deterministic keyword stand-in for the model, so CI can run the eval harness offline.

It reads the same prompts the real model gets and answers with simple rules. It is not a
measure of model quality; its job is to exercise the graph, rules, tools, and scoring.
"""

import re
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.db.models import TicketCategory
from app.observability.cost import UsageTracker
from app.schemas.agent import Citation, IntakeOutput, ProposedAction, ResolutionOutput

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_INTENT_RULES: list[tuple[TicketCategory, re.Pattern[str]]] = [
    (TicketCategory.CANCELLATION, re.compile(r"\bcancel", re.I)),
    (
        TicketCategory.REPLACEMENT,
        re.compile(r"\b(replace|replacement|exchange|send (me )?(a )?new one)\b", re.I),
    ),
    (TicketCategory.REFUND, re.compile(r"\b(refund|money back|reimburse)", re.I)),
    (
        TicketCategory.MISSING_ORDER,
        re.compile(
            r"\b(where is|where's|hasn't arrived|has not arrived|never arrived|not arrived|"
            r"late|tracking|still waiting)\b",
            re.I,
        ),
    ),
    (TicketCategory.BILLING, re.compile(r"\b(charged|charge|billing|invoice|payment)\b", re.I)),
]
_ACTION_FOR_INTENT = {
    "refund": ProposedAction.REFUND,
    "replacement": ProposedAction.REPLACEMENT,
    "cancellation": ProposedAction.CANCEL,
    "missing_order": ProposedAction.INFO,
    "billing": ProposedAction.INFO,
}
_ORDER_LINE = re.compile(r"^- (ORD-\d+) \| [^|]+ \| [^|]+ \| [^|]+ \| (.+)$", re.M)
_CUSTOMER_LINE = re.compile(r"^\[customer\] (.*)$", re.M)
_INTENT_LINE = re.compile(r"^Intent: (\w+)$", re.M)
_EXCERPT = re.compile(r"^\[source: ([^|]+) \| section: ([^\]]+)\]\n(.+)$", re.M)
_WORD = re.compile(r"[a-z]{4,}")


def _human(messages: list[BaseMessage]) -> str:
    return str(messages[-1].content)


class KeywordLLM:
    """Implements the StructuredGenerator protocol without any network call."""

    async def generate(
        self, schema: type[SchemaT], messages: list[BaseMessage], usage: UsageTracker
    ) -> SchemaT:
        answer: Any
        if schema is IntakeOutput:
            answer = self._intake(_human(messages))
        elif schema is ResolutionOutput:
            answer = self._resolution(_human(messages))
        else:
            raise TypeError(f"KeywordLLM cannot answer {schema.__name__}")
        usage.add({"input_tokens": 0, "output_tokens": 0})
        if not isinstance(answer, schema):
            raise TypeError(f"KeywordLLM built the wrong answer type for {schema.__name__}")
        return answer

    @staticmethod
    def _intake(text: str) -> IntakeOutput:
        customer = " ".join(_CUSTOMER_LINE.findall(text))
        intent = next(
            (intent for intent, rule in _INTENT_RULES if rule.search(customer)),
            TicketCategory.OTHER,
        )
        words = set(_WORD.findall(customer.lower()))
        matches = [
            order_id
            for order_id, items in _ORDER_LINE.findall(text)
            if words & set(_WORD.findall(items.lower()))
        ]
        return IntakeOutput(
            intent=intent,
            matching_order_ids=matches,
            confidence=0.9,
            summary=f"Keyword stand-in classified this as {intent.value}.",
        )

    @staticmethod
    def _resolution(text: str) -> ResolutionOutput:
        found = _INTENT_LINE.search(text)
        intent = found.group(1) if found else "other"
        action = _ACTION_FOR_INTENT.get(intent, ProposedAction.ESCALATE)
        excerpts = _EXCERPT.findall(text)
        citations = [Citation(source=s.strip(), section=sec.strip()) for s, sec, _ in excerpts[:1]]
        reply = None
        if action is ProposedAction.INFO:
            reply = excerpts[0][2][:300] if excerpts else "Here is what we know about your order."
        return ResolutionOutput(
            action=action,
            reason="Keyword stand-in proposal.",
            confidence=0.9,
            citations=citations,
            customer_reply=reply,
        )
