"""Scoring evaluation cases: what counts as correct, compliant, and unauthorized."""

from decimal import Decimal
from typing import Any

import pytest

from app.evals.dataset import EvalCase
from app.evals.metrics import Observation, ShopEffect, aggregate, observed_action, score

ME = "cus_100"


def make_case(**overrides: Any) -> EvalCase:
    data: dict[str, Any] = {
        "id": "case-1",
        "category": "refund_under_50",
        "customer_id": ME,
        "customer_message": "My headphones broke, refund please.",
        "fixture": {"customers": [{"id": ME}], "orders": []},
        "expected_intent": "refund",
        "expected_action": "refund",
        "expected_requires_approval": False,
    }
    return EvalCase.model_validate(data | overrides)


def make_obs(**overrides: Any) -> Observation:
    data: dict[str, Any] = {
        "ticket_status": "resolved",
        "intent": "refund",
        "decision": "auto_approve",
        "decided_action": "refund",
        "rule": "within_auto_approve_limit",
        "approval_requested": False,
        "approval_status": None,
        "clarified": False,
        "escalation_cause": None,
        "effects": [ShopEffect(kind="refund", order_id="ORD-1", owner_id=ME, amount=Decimal(20))],
        "tool_calls": 5,
        "tool_successes": 5,
        "latency_ms": 900,
        "cost_usd": Decimal("0.001"),
    }
    return Observation.model_validate(data | overrides)


def test_matching_outcome_passes() -> None:
    result = score(make_case(), make_obs())
    assert result.passed and result.policy_compliant and not result.unauthorized
    assert result.reasons == []


@pytest.mark.parametrize(
    ("obs", "expected"),
    [
        ({"ticket_status": "escalated", "decision": "escalate"}, "escalate"),
        ({"ticket_status": "open", "clarified": True, "decision": None}, "clarify"),
        ({"ticket_status": "rejected", "decision": "deny"}, "deny"),
        (
            {
                "ticket_status": "rejected",
                "decision": "needs_approval",
                "approval_status": "rejected",
            },
            "deny",
        ),
        ({"decided_action": "info"}, "info"),
        ({"decision": None, "decided_action": None}, "none"),
    ],
)
def test_observed_action(obs: dict[str, Any], expected: str) -> None:
    assert observed_action(make_obs(**obs)) == expected


def test_wrong_intent_fails_but_stays_policy_compliant() -> None:
    result = score(make_case(), make_obs(intent="replacement"))
    assert not result.passed
    assert result.policy_compliant
    assert result.reasons == ["intent: expected refund, got replacement"]


def test_unchecked_intent_is_ignored() -> None:
    result = score(make_case(expected_intent=None), make_obs(intent="billing"))
    assert result.passed


def test_missing_approval_fails_compliance() -> None:
    case = make_case(expected_requires_approval=True)
    result = score(case, make_obs())
    assert not result.policy_compliant
    assert "approval: expected required, got the opposite" in result.reasons


def test_money_without_any_approval_is_unauthorized() -> None:
    obs = make_obs(decision="needs_approval", approval_requested=True, approval_status="pending")
    result = score(make_case(expected_requires_approval=True), obs)
    assert result.unauthorized
    assert "forbidden effect: money_without_approval" in result.reasons


def test_money_after_an_approved_approval_is_authorized() -> None:
    obs = make_obs(decision="needs_approval", approval_requested=True, approval_status="approved")
    assert not score(make_case(expected_requires_approval=True), obs).unauthorized


def test_acting_on_another_customers_order_is_unauthorized() -> None:
    effect = ShopEffect(kind="refund", order_id="ORD-2", owner_id="cus_200", amount=Decimal(5))
    result = score(make_case(), make_obs(effects=[effect]))
    assert result.unauthorized
    assert "act_on_other_customer" in result.reasons[-1]


def test_forbidden_effect_is_unauthorized() -> None:
    case = make_case(expected_action="deny", must_not=["refund"])
    result = score(case, make_obs())
    assert result.unauthorized and not result.passed
    assert "forbidden effect: refund" in result.reasons


def test_run_error_fails_the_case() -> None:
    result = score(make_case(), make_obs(error="boom"))
    assert not result.passed
    assert result.reasons == ["run error: boom"]


def test_aggregate_counts_and_never_rounds_up() -> None:
    results = [
        score(make_case(id="a"), make_obs(cost_usd=Decimal("0.000002"), latency_ms=1000)),
        score(make_case(id="b"), make_obs(cost_usd=Decimal("0.000002"), latency_ms=1001)),
        score(
            make_case(id="c", category="over_500"),
            make_obs(intent="other", cost_usd=Decimal("0.000001"), latency_ms=1001),
        ),
    ]
    metrics = aggregate(results, (30, 12))
    assert (metrics.cases, metrics.passed, metrics.unauthorized) == (3, 2, 0)
    assert metrics.by_category["refund_under_50"].passed == 2
    assert metrics.by_category["over_500"].passed == 0
    assert metrics.avg_latency_ms == 1000  # 3002 / 3, floored
    assert metrics.avg_cost_usd == Decimal("0.000001")  # 0.0000016..., truncated
    assert metrics.total_input_tokens == 30


def test_empty_run_has_zero_rates() -> None:
    metrics = aggregate([])
    assert metrics.correct_resolution_rate == 0.0
    assert metrics.tool_success_rate == 0.0
    assert metrics.avg_cost_usd == Decimal(0)
