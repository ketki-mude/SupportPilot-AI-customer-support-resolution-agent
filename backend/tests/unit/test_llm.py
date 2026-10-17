"""The chat model factory, structured output with one retry, and usage cost."""

from decimal import Decimal
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, SecretStr

from app.agents.llm import LLMConfigError, LLMError, StructuredLLM, create_chat_model
from app.core.config import LLMProvider, Settings, get_settings
from app.observability.cost import UsageTracker


class Answer(BaseModel):
    value: int


class FakeStructured:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.received: list[list[BaseMessage]] = []

    async def ainvoke(self, messages: list[BaseMessage]) -> Any:
        self.received.append(messages)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeModel:
    def __init__(self, structured: FakeStructured) -> None:
        self.structured = structured

    def with_structured_output(self, schema: type[BaseModel], include_raw: bool) -> FakeStructured:
        assert include_raw
        return self.structured


def _raw(tokens_in: int = 10, tokens_out: int = 5) -> AIMessage:
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    )


def _llm(results: list[Any]) -> tuple[StructuredLLM, FakeStructured]:
    structured = FakeStructured(results)
    return StructuredLLM(lambda: FakeModel(structured)), structured  # type: ignore[arg-type,return-value]


async def test_valid_output_is_returned_and_usage_counted() -> None:
    llm, _ = _llm([{"raw": _raw(), "parsed": Answer(value=7), "parsing_error": None}])
    usage = UsageTracker()
    assert await llm.generate(Answer, [HumanMessage("q")], usage) == Answer(value=7)
    assert (usage.input_tokens, usage.output_tokens, usage.calls) == (10, 5, 1)


async def test_invalid_output_is_retried_once_with_a_correction() -> None:
    llm, structured = _llm(
        [
            {"raw": _raw(), "parsed": None, "parsing_error": ValueError("value missing")},
            {"raw": _raw(), "parsed": Answer(value=3), "parsing_error": None},
        ]
    )
    usage = UsageTracker()
    assert await llm.generate(Answer, [HumanMessage("q")], usage) == Answer(value=3)
    assert usage.calls == 2
    correction = structured.received[1][-1].content
    assert "did not match the required structure" in str(correction)
    assert "value missing" in str(correction)


async def test_two_invalid_outputs_raise() -> None:
    bad = {"raw": _raw(), "parsed": None, "parsing_error": ValueError("bad")}
    llm, _ = _llm([bad, dict(bad)])
    with pytest.raises(LLMError, match="did not return a valid Answer"):
        await llm.generate(Answer, [HumanMessage("q")], UsageTracker())


async def test_provider_errors_become_llm_errors() -> None:
    llm, _ = _llm([TimeoutError("deadline")])
    with pytest.raises(LLMError, match="Model call failed: TimeoutError"):
        await llm.generate(Answer, [HumanMessage("q")], UsageTracker())


def _settings(**overrides: Any) -> Settings:
    return get_settings().model_copy(update=overrides)


@pytest.mark.parametrize(
    ("provider", "key_field", "class_name"),
    [
        (LLMProvider.GEMINI, "google_api_key", "ChatGoogleGenerativeAI"),
        (LLMProvider.ANTHROPIC, "anthropic_api_key", "ChatAnthropic"),
        (LLMProvider.OPENAI, "openai_api_key", "ChatOpenAI"),
    ],
)
def test_factory_builds_the_configured_provider(
    provider: LLMProvider, key_field: str, class_name: str
) -> None:
    settings = _settings(
        llm_provider=provider, llm_model="some-model", **{key_field: SecretStr("k")}
    )
    model = create_chat_model(settings)
    assert type(model).__name__ == class_name
    assert model.temperature == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("provider", "env_name"),
    [
        (LLMProvider.GEMINI, "GOOGLE_API_KEY"),
        (LLMProvider.ANTHROPIC, "ANTHROPIC_API_KEY"),
        (LLMProvider.OPENAI, "OPENAI_API_KEY"),
    ],
)
def test_missing_key_names_the_variable(provider: LLMProvider, env_name: str) -> None:
    settings = _settings(
        llm_provider=provider, google_api_key=None, anthropic_api_key=None, openai_api_key=None
    )
    with pytest.raises(LLMConfigError, match=env_name):
        create_chat_model(settings)


async def test_missing_key_surfaces_on_first_use_not_at_startup() -> None:
    def factory() -> Any:
        raise LLMConfigError("Set GOOGLE_API_KEY to use this LLM provider")

    llm = StructuredLLM(factory)  # constructing must not fail
    with pytest.raises(LLMConfigError):
        await llm.generate(Answer, [HumanMessage("q")], UsageTracker())


def test_cost_uses_configured_prices() -> None:
    usage = UsageTracker()
    usage.add({"input_tokens": 1_000_000, "output_tokens": 200_000})
    settings = _settings(
        llm_input_usd_per_million=Decimal("0.30"), llm_output_usd_per_million=Decimal("2.50")
    )
    assert usage.cost(settings) == Decimal("0.800000")


def test_missing_usage_metadata_still_counts_the_call() -> None:
    usage = UsageTracker()
    usage.add(None)
    assert (usage.calls, usage.input_tokens) == (1, 0)
