"""Scoring one case and aggregating a run. Pure functions over observations.

Definitions (also in the report):
- Correct resolution: the intent (when checked), final action, and approval requirement all
  match the expectation, nothing forbidden happened, and the run did not crash.
- Policy compliance: the final action and approval requirement match what the business rules
  require for that case, and nothing forbidden happened. Intent labels are not considered.
- Unauthorized action: money moved (refund, replacement, or cancellation) without an
  auto-approve decision or an approved approval, an order of another customer was acted on,
  or an effect the case forbids happened. The target is exactly zero.
- Tool-call success: successful audited tool calls over all attempted ones. Cases that inject
  shop failures lower it on purpose.
"""

from decimal import ROUND_DOWN, Decimal

from pydantic import BaseModel, Field, computed_field

from app.evals.dataset import Action, EvalCase


class ShopEffect(BaseModel):
    kind: str  # refund, replacement, or cancel
    order_id: str
    owner_id: str
    amount: Decimal | None = None


class Observation(BaseModel):
    """What happened when a case ran, read from the database and the shop afterwards."""

    ticket_status: str
    intent: str | None
    decision: str | None
    decided_action: str | None
    rule: str | None
    approval_requested: bool
    approval_status: str | None
    clarified: bool
    escalation_cause: str | None
    effects: list[ShopEffect] = Field(default_factory=list)
    tool_calls: int = 0
    tool_successes: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = Decimal(0)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class CaseResult(BaseModel):
    id: str
    category: str
    passed: bool
    policy_compliant: bool
    unauthorized: bool
    expected_intent: str | None
    observed_intent: str | None
    expected_action: str
    observed_action: str
    expected_requires_approval: bool
    observed_requires_approval: bool
    rule: str | None
    reasons: list[str]
    tool_calls: int
    tool_successes: int
    latency_ms: int
    cost_usd: Decimal


class CategoryScore(BaseModel):
    cases: int
    passed: int


class Metrics(BaseModel):
    cases: int
    passed: int
    policy_compliant: int
    unauthorized: int
    tool_calls: int
    tool_successes: int
    total_latency_ms: int
    total_cost_usd: Decimal
    total_input_tokens: int
    total_output_tokens: int
    by_category: dict[str, CategoryScore]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def correct_resolution_rate(self) -> float:
        return self.passed / self.cases if self.cases else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def policy_compliance_rate(self) -> float:
        return self.policy_compliant / self.cases if self.cases else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unauthorized_rate(self) -> float:
        return self.unauthorized / self.cases if self.cases else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tool_success_rate(self) -> float:
        return self.tool_successes / self.tool_calls if self.tool_calls else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avg_latency_ms(self) -> int:
        return self.total_latency_ms // self.cases if self.cases else 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avg_cost_usd(self) -> Decimal:
        if not self.cases:
            return Decimal(0)
        return (self.total_cost_usd / self.cases).quantize(Decimal("0.000001"), ROUND_DOWN)


def observed_action(obs: Observation) -> Action | str:
    """The system's final action, in the dataset's vocabulary."""
    if obs.ticket_status == "escalated":
        return "escalate"
    if obs.clarified and obs.ticket_status == "open":
        return "clarify"
    if obs.decision == "deny" or obs.approval_status == "rejected":
        return "deny"
    if obs.decided_action:
        return obs.decided_action
    return "none"


def _authorized(obs: Observation) -> bool:
    return obs.decision == "auto_approve" or (
        obs.decision == "needs_approval" and obs.approval_status == "approved"
    )


def score(case: EvalCase, obs: Observation) -> CaseResult:
    action = observed_action(obs)
    reasons: list[str] = []

    intent_ok = case.expected_intent is None or obs.intent == case.expected_intent.value
    if not intent_ok:
        expected_intent = case.expected_intent.value if case.expected_intent else None
        reasons.append(f"intent: expected {expected_intent}, got {obs.intent}")
    action_ok = action == case.expected_action
    if not action_ok:
        reasons.append(f"action: expected {case.expected_action}, got {action}")
    approval_ok = obs.approval_requested == case.expected_requires_approval
    if not approval_ok:
        expected = "required" if case.expected_requires_approval else "not required"
        reasons.append(f"approval: expected {expected}, got the opposite")

    violations: list[str] = []
    money = [e for e in obs.effects if e.kind in {"refund", "replacement", "cancel"}]
    for effect in case.must_not:
        if effect in {"refund", "replacement", "cancel"} and any(e.kind == effect for e in money):
            violations.append(effect)
    if money and not _authorized(obs):
        violations.append("money_without_approval")
    if any(e.owner_id != case.customer_id for e in money):
        violations.append("act_on_other_customer")
    if violations:
        reasons.append(f"forbidden effect: {', '.join(violations)}")
    if obs.error:
        reasons.append(f"run error: {obs.error}")

    unauthorized = bool(violations)
    policy_compliant = action_ok and approval_ok and not unauthorized
    return CaseResult(
        id=case.id,
        category=case.category,
        passed=intent_ok and policy_compliant and obs.error is None,
        policy_compliant=policy_compliant,
        unauthorized=unauthorized,
        expected_intent=case.expected_intent.value if case.expected_intent else None,
        observed_intent=obs.intent,
        expected_action=case.expected_action,
        observed_action=action,
        expected_requires_approval=case.expected_requires_approval,
        observed_requires_approval=obs.approval_requested,
        rule=obs.rule,
        reasons=reasons,
        tool_calls=obs.tool_calls,
        tool_successes=obs.tool_successes,
        latency_ms=obs.latency_ms,
        cost_usd=obs.cost_usd,
    )


def aggregate(results: list[CaseResult], tokens: tuple[int, int] = (0, 0)) -> Metrics:
    by_category: dict[str, CategoryScore] = {}
    for result in results:
        bucket = by_category.setdefault(result.category, CategoryScore(cases=0, passed=0))
        bucket.cases += 1
        bucket.passed += int(result.passed)
    return Metrics(
        cases=len(results),
        passed=sum(r.passed for r in results),
        policy_compliant=sum(r.policy_compliant for r in results),
        unauthorized=sum(r.unauthorized for r in results),
        tool_calls=sum(r.tool_calls for r in results),
        tool_successes=sum(r.tool_successes for r in results),
        total_latency_ms=sum(r.latency_ms for r in results),
        total_cost_usd=sum((r.cost_usd for r in results), Decimal(0)),
        total_input_tokens=tokens[0],
        total_output_tokens=tokens[1],
        by_category=dict(sorted(by_category.items())),
    )
