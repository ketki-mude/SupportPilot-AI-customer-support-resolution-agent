"""Provider-swappable chat models and schema-validated structured output."""

from collections.abc import Callable
from functools import cached_property
from typing import Any, Protocol, TypeVar

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, SecretStr

from app.core.config import LLMProvider, Settings
from app.observability.cost import UsageTracker

logger = structlog.get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_ATTEMPTS = 2  # the first answer plus one automatic retry on invalid output


class LLMError(Exception):
    """The model could not produce a usable answer. The agent escalates."""


class LLMConfigError(LLMError):
    """The configured provider is missing its API key."""


class StructuredGenerator(Protocol):
    """Anything that turns messages into a validated instance of a schema."""

    async def generate(
        self, schema: type[SchemaT], messages: list[BaseMessage], usage: UsageTracker
    ) -> SchemaT: ...


def create_chat_model(settings: Settings) -> BaseChatModel:
    """Build the chat model for LLM_PROVIDER, at temperature 0 unless configured otherwise."""
    common: dict[str, Any] = {"timeout": settings.llm_timeout_seconds, "max_retries": 2}
    if settings.llm_temperature is not None:
        common["temperature"] = settings.llm_temperature
    match settings.llm_provider:
        case LLMProvider.GEMINI:
            from langchain_google_genai import ChatGoogleGenerativeAI

            key = _require(settings.google_api_key, "GOOGLE_API_KEY")
            return ChatGoogleGenerativeAI(model=settings.llm_model, google_api_key=key, **common)
        case LLMProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            key = _require(settings.anthropic_api_key, "ANTHROPIC_API_KEY")
            return ChatAnthropic(model_name=settings.llm_model, api_key=key, **common)
        case LLMProvider.OPENAI:
            from langchain_openai import ChatOpenAI

            key = _require(settings.openai_api_key, "OPENAI_API_KEY")
            return ChatOpenAI(model=settings.llm_model, api_key=key, **common)


def _require(secret: SecretStr | None, env_name: str) -> SecretStr:
    if secret is None:
        raise LLMConfigError(f"Set {env_name} to use this LLM provider")
    return secret


class StructuredLLM:
    """Asks the model for output matching a Pydantic schema, retrying once if it is invalid.

    The model is created on first use, so the API starts even before a key is configured.
    """

    def __init__(self, model_factory: Callable[[], BaseChatModel]) -> None:
        self._model_factory = model_factory

    @cached_property
    def _model(self) -> BaseChatModel:
        return self._model_factory()

    async def generate(
        self, schema: type[SchemaT], messages: list[BaseMessage], usage: UsageTracker
    ) -> SchemaT:
        try:
            structured = self._model.with_structured_output(schema, include_raw=True)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Model setup failed: {exc}") from exc

        conversation = list(messages)
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                result = await structured.ainvoke(conversation)
            except Exception as exc:
                raise LLMError(f"Model call failed: {type(exc).__name__}: {exc}") from exc
            if not isinstance(result, dict):  # include_raw=True always returns a dict
                raise LLMError("Unexpected structured output shape")
            raw = result.get("raw")
            usage.add(getattr(raw, "usage_metadata", None))
            parsed = result.get("parsed")
            if isinstance(parsed, schema):
                return parsed
            error = result.get("parsing_error")
            logger.warning(
                "llm_invalid_output", schema=schema.__name__, attempt=attempt, error=str(error)
            )
            conversation = [
                *messages,
                HumanMessage(
                    f"Your previous answer did not match the required structure ({error}). "
                    "Answer again, following the structure exactly."
                ),
            ]
        raise LLMError(f"The model did not return a valid {schema.__name__}")
