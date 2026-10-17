"""Guardrails around the model: PII redaction, prompt-injection heuristics, and the final check
that stops the executor from acting without an approval or an auto-approve decision."""

import re
from dataclasses import dataclass
from typing import Any

from app.db.models import Approval, ApprovalStatus
from app.schemas.agent import Decision, PolicyDecision, ProposedAction

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 13 to 19 digits, optionally grouped by spaces or dashes; confirmed with the Luhn check.
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_PHONE = re.compile(
    r"(?<![\w-])(?:\+?\d{1,2}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b"
)

_INJECTION_PATTERNS = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(rules?|instructions?|polic(y|ies)"
        r"|guidelines?|prompts?|limits?)\b",
        re.IGNORECASE,
    ),
    "role_change": re.compile(
        r"\b(you are now|act as|pretend (to be|you are)|from now on you"
        r"|developer mode|jailbreak)\b",
        re.IGNORECASE,
    ),
    "prompt_probe": re.compile(
        r"\b(system prompt|hidden instructions|your instructions|reveal your)\b", re.IGNORECASE
    ),
    "authority_claim": re.compile(
        r"\b(i am|i'm|as) (an? )?(admin|administrator|supervisor|manager|developer|acme staff)\b"
        r".{0,60}\b(approve|authori[sz]e|refund|override)\b",
        re.IGNORECASE,
    ),
    "fake_role_tag": re.compile(
        r"(^|\n)\s*(system|assistant|developer|admin)\s*:|</?\s*(system|instructions?|prompt)\s*>",
        re.IGNORECASE,
    ),
    "forced_approval": re.compile(
        r"\b(auto[- ]?approve|approve (it|this|my refund) (now|immediately)"
        r"|skip (the )?(approval|review)|(already|pre)[- ]?approved)\b",
        re.IGNORECASE,
    ),
}

EXECUTABLE_ACTIONS = frozenset(
    {ProposedAction.REFUND, ProposedAction.REPLACEMENT, ProposedAction.CANCEL, ProposedAction.INFO}
)


class UnauthorizedActionError(Exception):
    """The executor was asked to act without a recorded decision that allows it."""


def redact_pii(text: str) -> str:
    """Mask emails, card numbers, and phone numbers before text reaches the model or logs."""
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CARD.sub(lambda m: "[CARD]" if _luhn_valid(m.group()) else m.group(), text)
    return _PHONE.sub("[PHONE]", text)


_MAX_LOGGED_CHARS = 200


def redact_value(value: Any) -> Any:
    """Redact and shorten every string in a nested value, for logging untrusted arguments."""
    if isinstance(value, str):
        text = redact_pii(value)
        return text if len(text) <= _MAX_LOGGED_CHARS else text[:_MAX_LOGGED_CHARS] + "…"
    if isinstance(value, dict):
        return {str(k): redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return value


def detect_injection(text: str) -> list[str]:
    """Names of the injection patterns found in customer text; empty when it looks benign."""
    return [name for name, pattern in _INJECTION_PATTERNS.items() if pattern.search(text)]


@dataclass(frozen=True, slots=True)
class Authorization:
    """Why the executor may proceed: an auto-approve decision or an approved approval row."""

    decision: PolicyDecision
    approval_id: str | None = None


def authorize_action(decision: PolicyDecision | None, approval: Approval | None) -> Authorization:
    """Refuse anything that the policy engine did not auto-approve or a supervisor did not approve.

    The approval row is read from the database by the caller, never taken from model output.
    """
    if decision is None:
        raise UnauthorizedActionError("No policy decision is recorded for this action")
    if decision.action not in EXECUTABLE_ACTIONS:
        raise UnauthorizedActionError(f"{decision.action.value} is not an executable action")
    if decision.decision is Decision.AUTO_APPROVE:
        return Authorization(decision)
    if decision.decision is not Decision.NEEDS_APPROVAL:
        raise UnauthorizedActionError(f"A {decision.decision.value} decision cannot be executed")
    if approval is None or approval.status is not ApprovalStatus.APPROVED:
        raise UnauthorizedActionError("The action needs a supervisor approval that is not recorded")
    if approval.proposed_action.get(
        "action"
    ) != decision.action.value or approval.proposed_action.get("amount") != (
        None if decision.amount is None else f"{decision.amount:.2f}"
    ):
        raise UnauthorizedActionError("The approved action does not match the decision")
    return Authorization(decision, approval_id=str(approval.id))


def _luhn_valid(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0
