"""Circuit breaker, rate limiter, trace scrubbing, and argument redaction."""

import os

import pytest
from pydantic import SecretStr

from app.agents.guardrails import redact_value
from app.agents.tools.commerce import CircuitBreaker, CircuitState, CommerceUnavailableError
from app.core.config import get_settings
from app.core.rate_limit import SlidingWindowLimiter
from app.observability.tracing import build_tracer, disable_ambient_tracing, scrubbed


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_breaker_opens_after_consecutive_failures(clock: Clock) -> None:
    breaker = CircuitBreaker(threshold=3, cooldown=30, clock=clock)
    for _ in range(2):
        breaker.before_call()
        breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CommerceUnavailableError) as caught:
        breaker.before_call()
    assert caught.value.code == "circuit_open"


def test_success_resets_the_failure_count(clock: Clock) -> None:
    breaker = CircuitBreaker(threshold=2, cooldown=30, clock=clock)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED


def test_after_the_cooldown_one_trial_call_decides(clock: Clock) -> None:
    breaker = CircuitBreaker(threshold=1, cooldown=30, clock=clock)
    breaker.record_failure()
    clock.now += 30
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.before_call()  # the trial
    with pytest.raises(CommerceUnavailableError):
        breaker.before_call()  # everyone else waits for it
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_a_failed_trial_reopens_the_circuit(clock: Clock) -> None:
    breaker = CircuitBreaker(threshold=5, cooldown=30, clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.now += 31
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    clock.now += 29
    assert breaker.state is CircuitState.OPEN


def test_rate_limiter_slides(clock: Clock) -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, clock=clock)
    assert limiter.hit("a") is None
    clock.now += 10
    assert limiter.hit("a") is None
    assert limiter.hit("a") == pytest.approx(50)
    assert limiter.hit("b") is None  # keys are independent
    clock.now += 51
    assert limiter.hit("a") is None  # the first hit has left the window


def test_tracing_is_off_without_the_flag_or_a_key() -> None:
    settings = get_settings()
    assert build_tracer(settings.model_copy(update={"langsmith_tracing": False})) is None
    assert (
        build_tracer(
            settings.model_copy(update={"langsmith_tracing": True, "langsmith_api_key": None})
        )
        is None
    )


def test_tracing_uses_the_configured_project() -> None:
    tracer = build_tracer(
        get_settings().model_copy(
            update={
                "langsmith_tracing": True,
                "langsmith_api_key": SecretStr("lsv2-test"),
                "langsmith_project": "supportpilot-test",
            }
        )
    )
    assert tracer is not None
    assert tracer.project_name == "supportpilot-test"  # type: ignore[attr-defined]


def test_traces_are_scrubbed_of_personal_data() -> None:
    trace = {
        "inputs": {"text": "Email jordan@example.com or call 555-010-1234"},
        "outputs": [{"customer": {"phone": "+1 555 010 1234", "card": "4242 4242 4242 4242"}}],
    }
    assert scrubbed(trace) == {
        "inputs": {"text": "Email [EMAIL] or call [PHONE]"},
        "outputs": [{"customer": {"phone": "[PHONE]", "card": "[CARD]"}}],
    }


def test_ambient_tracing_is_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    disable_ambient_tracing()
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_untrusted_arguments_are_redacted_and_shortened() -> None:
    value = {"reason": "mail me@example.com " + "y" * 300, "skus": ["call 555-010-1234"], "n": 3}
    redacted = redact_value(value)
    assert redacted["reason"].startswith("mail [EMAIL] ")
    assert len(redacted["reason"]) == 201
    assert redacted["skus"] == ["call [PHONE]"]
    assert redacted["n"] == 3
