"""HTTP client for the Acme commerce API with timeouts, retries, a circuit breaker, and typed
errors.

Only transient failures (timeouts, connection errors, 502/503/504) are retried. Everything the
API deliberately refuses (4xx) surfaces immediately as a typed error. When calls keep failing,
the circuit opens and calls fail fast, so the agent escalates instead of making customers wait.
"""

import asyncio
import time
from collections.abc import Callable
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self, TypeVar
from urllib.parse import quote

import httpx
import structlog
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings
from app.schemas.commerce import Cancellation, Customer, Order, Refund, Replacement

logger = structlog.get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

_RETRYABLE_STATUS = frozenset({502, 503, 504})
_MAX_BACKOFF_SECONDS = 10


class CommerceError(Exception):
    """Base for every failure talking to the commerce API."""

    def __init__(self, message: str, *, code: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CommerceUnavailableError(CommerceError):
    """The API is down or broken even after retries. The agent should escalate."""


class CommerceNotFoundError(CommerceError):
    """The customer or order does not exist."""


class CommerceRejectedError(CommerceError):
    """The API refused the request, for example a second remedy on the same order."""


class _TransientError(Exception):
    """A failure worth retrying."""


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Opens after `threshold` failed calls in a row, then fails fast for `cooldown` seconds.

    After the cooldown one trial call is let through: success closes the circuit, failure opens
    it again. Only unavailability counts; a 404 or a business refusal means the service is up.
    """

    def __init__(
        self, threshold: int, cooldown: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._clock() - self._opened_at >= self.cooldown:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def before_call(self) -> None:
        state = self.state
        if state is CircuitState.OPEN or (
            state is CircuitState.HALF_OPEN and self._trial_in_flight
        ):
            raise CommerceUnavailableError(
                "Commerce API calls are paused after repeated failures", code="circuit_open"
            )
        if state is CircuitState.HALF_OPEN:
            self._trial_in_flight = True

    def record_success(self) -> None:
        if self._opened_at is not None:
            logger.info("commerce_circuit_closed")
        self._failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    def record_failure(self) -> None:
        self._failures += 1
        reopen = self.state is CircuitState.HALF_OPEN
        self._trial_in_flight = False
        if reopen or self._failures >= self.threshold:
            self._opened_at = self._clock()
            logger.warning(
                "commerce_circuit_opened", failures=self._failures, cooldown_s=self.cooldown
            )


class CommerceClient:
    """Async client for one commerce API base URL. Use as an async context manager."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        max_attempts: int,
        backoff_seconds: float,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self.breaker = breaker or CircuitBreaker(threshold=5, cooldown=30)
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> Self:
        return cls(
            settings.mock_commerce_url,
            timeout_seconds=settings.commerce_timeout_seconds,
            max_attempts=settings.commerce_max_attempts,
            backoff_seconds=settings.commerce_backoff_seconds,
            breaker=CircuitBreaker(
                settings.commerce_breaker_threshold, settings.commerce_breaker_cooldown_seconds
            ),
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_customer(self, customer_id: str) -> Customer:
        data = await self._request("GET", f"/customers/{_segment(customer_id)}")
        return _load(Customer, data)

    async def list_orders(self, customer_id: str) -> list[Order]:
        data = await self._request("GET", f"/customers/{_segment(customer_id)}/orders")
        if not isinstance(data, list):
            raise CommerceUnavailableError(
                "Commerce API returned an unexpected order list", code="invalid_response"
            )
        return [_load(Order, item) for item in data]

    async def get_order(self, order_id: str) -> Order:
        data = await self._request("GET", f"/orders/{_segment(order_id)}")
        return _load(Order, data)

    async def create_refund(
        self, *, order_id: str, amount: Decimal, reason: str, idempotency_key: str
    ) -> Refund:
        body = {"order_id": order_id, "amount": f"{amount:.2f}", "reason": reason}
        data = await self._request("POST", "/refunds", json=body, idempotency_key=idempotency_key)
        return _load(Refund, data)

    async def create_replacement(
        self, *, order_id: str, skus: list[str] | None, reason: str, idempotency_key: str
    ) -> Replacement:
        body: dict[str, Any] = {"order_id": order_id, "reason": reason}
        if skus:
            body["skus"] = skus
        data = await self._request(
            "POST", "/replacements", json=body, idempotency_key=idempotency_key
        )
        return _load(Replacement, data)

    async def cancel_order(
        self, *, order_id: str, reason: str, idempotency_key: str
    ) -> Cancellation:
        data = await self._request(
            "POST",
            f"/orders/{_segment(order_id)}/cancel",
            json={"reason": reason},
            idempotency_key=idempotency_key,
        )
        return _load(Cancellation, data)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Send with retries on transient failures, then map the response to data or an error."""
        self.breaker.before_call()
        try:
            data = await self._request_with_retries(method, path, json, idempotency_key)
        except CommerceUnavailableError:
            self.breaker.record_failure()
            raise
        except CommerceError:
            self.breaker.record_success()  # the API answered; it refused this request
            raise
        self.breaker.record_success()
        return data

    async def _request_with_retries(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}

        def log_retry(state: RetryCallState) -> None:
            error = state.outcome.exception() if state.outcome else None
            logger.warning(
                "commerce_retry",
                method=method,
                path=path,
                attempt=state.attempt_number,
                error=str(error),
            )

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._backoff, max=_MAX_BACKOFF_SECONDS),
            retry=retry_if_exception_type(_TransientError),
            before_sleep=log_retry,
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    response = await self._send(method, path, json, headers)
        except _TransientError as exc:
            logger.error(
                "commerce_unavailable",
                method=method,
                path=path,
                attempts=self._max_attempts,
                error=str(exc),
            )
            raise CommerceUnavailableError(
                f"Commerce API unavailable after {self._max_attempts} attempts: {exc}",
                code="commerce_unavailable",
            ) from exc
        return _parse(response)

    async def _send(
        self, method: str, path: str, json: dict[str, Any] | None, headers: dict[str, str]
    ) -> httpx.Response:
        """One attempt, bounded by the timeout even when the transport ignores it."""
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._http.request(method, path, json=json, headers=headers)
        except TimeoutError as exc:
            raise _TransientError(f"timed out after {self._timeout}s") from exc
        except httpx.TransportError as exc:
            raise _TransientError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code in _RETRYABLE_STATUS:
            raise _TransientError(f"HTTP {response.status_code}")
        return response


def _segment(value: str) -> str:
    """Escape an id for use as one URL path segment, so it cannot change the route."""
    return quote(value, safe="")


def _parse(response: httpx.Response) -> Any:
    if response.is_success:
        return response.json()
    code, message = _error_details(response)
    status = response.status_code
    if status == 404:
        raise CommerceNotFoundError(message, code=code, status_code=status)
    if status < 500:
        raise CommerceRejectedError(message, code=code, status_code=status)
    raise CommerceUnavailableError(message, code=code, status_code=status)


def _error_details(response: httpx.Response) -> tuple[str, str]:
    """Read the API's {"error": {"code", "message"}} envelope, tolerating anything else."""
    try:
        error = response.json()["error"]
        return str(error["code"]), str(error["message"])
    except (ValueError, KeyError, TypeError):
        return f"http_{response.status_code}", response.reason_phrase


def _load(model: type[ModelT], data: Any) -> ModelT:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise CommerceUnavailableError(
            f"Commerce API returned an unexpected {model.__name__}", code="invalid_response"
        ) from exc
