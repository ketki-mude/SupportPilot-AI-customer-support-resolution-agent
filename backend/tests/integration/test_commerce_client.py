"""The commerce client against the real mock API running in-process, including injected faults."""

import logging
import time
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.agents.tools.commerce import (
    CommerceClient,
    CommerceNotFoundError,
    CommerceRejectedError,
    CommerceUnavailableError,
)
from app.schemas.commerce import OrderStatus, PaymentStatus
from tests.conftest import CommerceFactory


class CountingTransport(httpx.AsyncBaseTransport):
    """Counts requests that reach the wrapped transport."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return await self.inner.handle_async_request(request)


class FlakyTransport(CountingTransport):
    """Answers 503 for the first `failures` requests, then passes through."""

    def __init__(self, inner: httpx.AsyncBaseTransport, failures: int) -> None:
        super().__init__(inner)
        self.failures = failures

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.failures:
            return httpx.Response(503, json={"error": {"code": "busy", "message": "busy"}})
        return await self.inner.handle_async_request(request)


def counting(store: dict[str, CountingTransport]) -> Any:
    def wrap(inner: httpx.AsyncBaseTransport) -> httpx.AsyncBaseTransport:
        store["transport"] = CountingTransport(inner)
        return store["transport"]

    return wrap


def _client(handler: Any) -> CommerceClient:
    return CommerceClient(
        "http://mock-commerce",
        timeout_seconds=1,
        max_attempts=3,
        backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )


async def test_lookups_return_typed_models(make_commerce: CommerceFactory) -> None:
    client = make_commerce()
    customer = await client.get_customer("cus_001")
    order = await client.get_order("ORD-10001")
    assert customer.name == "Jordan Lee"
    assert order.status is OrderStatus.DELIVERED
    assert order.payment_status is PaymentStatus.CAPTURED
    assert order.total == Decimal("79.99")


async def test_unknown_order_raises_not_found(make_commerce: CommerceFactory) -> None:
    with pytest.raises(CommerceNotFoundError) as caught:
        await make_commerce().get_order("ORD-00000")
    assert caught.value.code == "order_not_found"
    assert caught.value.status_code == 404


async def test_ids_cannot_change_the_route(make_commerce: CommerceFactory) -> None:
    with pytest.raises(CommerceNotFoundError):
        await make_commerce().get_order("../customers/cus_001")


async def test_refund_is_idempotent_by_key(make_commerce: CommerceFactory, mock_store: Any) -> None:
    client = make_commerce()
    kwargs = {"order_id": "ORD-10001", "amount": Decimal("79.99"), "reason": "Damaged"}
    first = await client.create_refund(**kwargs, idempotency_key="refund-t1")
    second = await client.create_refund(**kwargs, idempotency_key="refund-t1")
    assert first == second
    assert mock_store.orders["ORD-10001"].refunded_amount == Decimal("79.99")


async def test_business_rejection_is_typed_and_not_retried(make_commerce: CommerceFactory) -> None:
    seen: dict[str, CountingTransport] = {}
    client = make_commerce(wrap=counting(seen))
    with pytest.raises(CommerceRejectedError) as caught:
        await client.create_refund(
            order_id="ORD-10005", amount=Decimal("1.00"), reason="Again", idempotency_key="k"
        )
    assert caught.value.code == "remedy_already_applied"
    assert caught.value.status_code == 409
    assert seen["transport"].calls == 1


async def test_cancel_and_replacement(make_commerce: CommerceFactory) -> None:
    client = make_commerce()
    cancellation = await client.cancel_order(
        order_id="ORD-10007", reason="Changed my mind", idempotency_key="cancel-t1"
    )
    assert cancellation.payment_action == "refunded"
    assert cancellation.refund is not None
    replacement = await client.create_replacement(
        order_id="ORD-10002", skus=["SKU-PC-110"], reason="Cracked", idempotency_key="rep-t1"
    )
    assert [i.sku for i in replacement.items] == ["SKU-PC-110"]


async def test_persistent_503_retries_then_raises_and_logs(
    make_commerce: CommerceFactory, caplog: pytest.LogCaptureFixture
) -> None:
    seen: dict[str, CountingTransport] = {}
    client = make_commerce(failure_rate=1.0, slow_fraction=0.0, wrap=counting(seen))
    with caplog.at_level(logging.WARNING), pytest.raises(CommerceUnavailableError) as caught:
        await client.get_order("ORD-10001")

    assert caught.value.code == "commerce_unavailable"
    assert seen["transport"].calls == 3
    events = [r.msg.get("event") for r in caplog.records if isinstance(r.msg, dict)]
    assert events.count("commerce_retry") == 2
    assert events.count("commerce_unavailable") == 1


async def test_transient_failures_recover(make_commerce: CommerceFactory) -> None:
    seen: dict[str, FlakyTransport] = {}

    def flaky(inner: httpx.AsyncBaseTransport) -> httpx.AsyncBaseTransport:
        seen["transport"] = FlakyTransport(inner, failures=2)
        return seen["transport"]

    order = await make_commerce(wrap=flaky).get_order("ORD-10001")
    assert order.order_id == "ORD-10001"
    assert seen["transport"].calls == 3


async def test_retried_refund_with_same_key_refunds_once(
    make_commerce: CommerceFactory, mock_store: Any
) -> None:
    client = make_commerce(failure_rate=0.5, slow_fraction=0.0, random_seed=11, max_attempts=10)
    kwargs = {"order_id": "ORD-10002", "amount": Decimal("29.98"), "reason": "Wrong color"}
    refunds = [await client.create_refund(**kwargs, idempotency_key="refund-t9") for _ in range(4)]
    assert len({r.refund_id for r in refunds}) == 1
    assert mock_store.orders["ORD-10002"].refunded_amount == Decimal("29.98")


async def test_slow_responses_time_out_and_count_as_transient(
    make_commerce: CommerceFactory,
) -> None:
    client = make_commerce(
        failure_rate=1.0, slow_fraction=1.0, slow_seconds=2.0, timeout_seconds=0.05, max_attempts=2
    )
    started = time.perf_counter()
    with pytest.raises(CommerceUnavailableError, match="timed out"):
        await client.get_order("ORD-10001")
    assert time.perf_counter() - started < 1.0


async def test_connection_errors_are_retried() -> None:
    calls = 0

    def refuse(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused", request=request)

    async with _client(refuse) as client:
        with pytest.raises(CommerceUnavailableError, match="ConnectError"):
            await client.get_customer("cus_001")
    assert calls == 3


async def test_server_errors_are_not_retried() -> None:
    calls = 0

    def crash(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="boom")

    async with _client(crash) as client:
        with pytest.raises(CommerceUnavailableError) as caught:
            await client.get_customer("cus_001")
    assert caught.value.code == "http_500"
    assert calls == 1


async def test_malformed_response_is_unavailable() -> None:
    async with _client(lambda _: httpx.Response(200, json={"unexpected": True})) as client:
        with pytest.raises(CommerceUnavailableError) as caught:
            await client.get_order("ORD-10001")
    assert caught.value.code == "invalid_response"
