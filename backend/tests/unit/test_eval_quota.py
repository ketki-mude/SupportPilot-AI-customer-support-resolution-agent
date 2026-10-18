"""Rate-limit backoff for evaluation runs."""

from typing import Any

import pytest
from langchain_core.messages import BaseMessage, HumanMessage

from app.agents.llm import LLMError
from app.evals.quota import RetryingLLM, is_rate_limited, suggested_delay
from app.observability.cost import UsageTracker
from app.schemas.agent import IntakeOutput

ANSWER = IntakeOutput(intent="refund", matching_order_ids=[], confidence=0.9, summary="s")
RATE_LIMITED = LLMError(
    "Model call failed: ChatGoogleGenerativeAIError: 429 RESOURCE_EXHAUSTED {'retryDelay': '7s'}"
)
DAILY_QUOTA = LLMError(
    "Model call failed: 429 RESOURCE_EXHAUSTED quota exceeded {'retryDelay': '28853s'}"
)


class Flaky:
    def __init__(self, *failures: Exception) -> None:
        self.failures = list(failures)
        self.calls = 0

    async def generate(self, schema: Any, messages: list[BaseMessage], usage: UsageTracker) -> Any:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ANSWER


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


async def _generate(llm: RetryingLLM) -> Any:
    return await llm.generate(IntakeOutput, [HumanMessage("hi")], UsageTracker())


def test_rate_limit_detection() -> None:
    assert is_rate_limited(RATE_LIMITED)
    assert is_rate_limited(LLMError("You exceeded your current quota"))
    assert not is_rate_limited(LLMError("The model did not return a valid IntakeOutput"))
    assert suggested_delay(RATE_LIMITED) == 7.0
    assert suggested_delay(LLMError("429 Rate limit reached. Please try again in 20s.")) == 20.0
    assert suggested_delay(LLMError("429 Please try again in 650ms.")) == 0.65
    assert suggested_delay(LLMError("429")) is None


async def test_waits_out_per_minute_limits() -> None:
    sleeps = Sleeps()
    inner = Flaky(RATE_LIMITED, LLMError("429 Too Many Requests"))
    llm = RetryingLLM(inner, base_delay=2.0, sleep=sleeps)
    assert await _generate(llm) is ANSWER
    assert sleeps.waits == [7.0, 4.0]  # the provider's hint, then exponential backoff
    assert llm.retries == 2 and llm.exhausted is None


async def test_other_errors_are_not_retried() -> None:
    sleeps = Sleeps()
    llm = RetryingLLM(Flaky(LLMError("Model setup failed")), sleep=sleeps)
    with pytest.raises(LLMError):
        await _generate(llm)
    assert sleeps.waits == [] and llm.exhausted is None


async def test_daily_quota_stops_instead_of_waiting() -> None:
    sleeps = Sleeps()
    llm = RetryingLLM(Flaky(DAILY_QUOTA), max_wait=120, sleep=sleeps)
    with pytest.raises(LLMError):
        await _generate(llm)
    assert sleeps.waits == []
    assert llm.exhausted == "Model quota exhausted; the provider asks to wait 28853s"


async def test_an_account_without_credit_stops_at_once() -> None:
    sleeps = Sleeps()
    no_credit = LLMError("429 You exceeded your current quota. 'code': 'insufficient_quota'")
    llm = RetryingLLM(Flaky(no_credit), sleep=sleeps)
    with pytest.raises(LLMError):
        await _generate(llm)
    assert sleeps.waits == []
    assert llm.exhausted == "The model account has no credit left (insufficient_quota)"


async def test_persistent_limits_stop_after_the_last_retry() -> None:
    sleeps = Sleeps()
    inner = Flaky(*[LLMError("429")] * 3)
    llm = RetryingLLM(inner, max_retries=2, base_delay=1.0, sleep=sleeps)
    with pytest.raises(LLMError):
        await _generate(llm)
    assert inner.calls == 3 and sleeps.waits == [1.0, 2.0]
    assert llm.exhausted == "Model still rate limited after 2 retries"
