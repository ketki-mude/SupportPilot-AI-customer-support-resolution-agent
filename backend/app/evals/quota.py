"""Backoff for model rate limits during evaluations.

Per-minute limits are waited out and the call retried. A limit that would take longer than
`max_wait` to clear (a daily quota) stops the evaluation instead of quietly turning every
remaining case into an escalation, which would make the report misleading.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.agents.llm import LLMError, StructuredGenerator
from app.observability.cost import UsageTracker

logger = structlog.get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_RATE_LIMITED = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|rate limit|quota", re.IGNORECASE)
# Gemini: retryDelay: '7s'. OpenAI: "Please try again in 20s" or "in 650ms".
_RETRY_DELAY = re.compile(r"retry_?delay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", re.IGNORECASE)
_TRY_AGAIN = re.compile(r"try again in (\d+(?:\.\d+)?)(ms|s)\b", re.IGNORECASE)
# Out of credit or billing: waiting does not help.
_NO_CREDIT = re.compile(r"insufficient_quota|billing", re.IGNORECASE)


def is_rate_limited(error: Exception) -> bool:
    return bool(_RATE_LIMITED.search(str(error)))


def suggested_delay(error: Exception) -> float | None:
    """The provider's own retry hint, when the error carries one."""
    text = str(error)
    if found := _RETRY_DELAY.search(text):
        return float(found.group(1))
    if found := _TRY_AGAIN.search(text):
        seconds = float(found.group(1))
        return seconds / 1000 if found.group(2).lower() == "ms" else seconds
    return None


class RetryingLLM:
    """Wraps a StructuredGenerator, retrying rate-limited calls with exponential backoff."""

    def __init__(
        self,
        inner: StructuredGenerator,
        *,
        max_retries: int = 5,
        base_delay: float = 2.0,
        max_wait: float = 120.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_wait = max_wait
        self._sleep = sleep
        self.exhausted: str | None = None
        self.retries = 0

    async def generate(
        self, schema: type[SchemaT], messages: list[BaseMessage], usage: UsageTracker
    ) -> SchemaT:
        for attempt in range(self._max_retries + 1):
            try:
                return await self._inner.generate(schema, messages, usage)
            except LLMError as exc:
                if not is_rate_limited(exc):
                    raise
                if _NO_CREDIT.search(str(exc)):
                    self.exhausted = "The model account has no credit left (insufficient_quota)"
                    raise
                if attempt == self._max_retries:
                    self.exhausted = f"Model still rate limited after {attempt} retries"
                    raise
                wait = suggested_delay(exc) or self._base_delay * 2**attempt
                if wait > self._max_wait:
                    self.exhausted = f"Model quota exhausted; the provider asks to wait {wait:.0f}s"
                    raise
                self.retries += 1
                logger.warning("eval_rate_limited", attempt=attempt + 1, wait_s=wait)
                await self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover
