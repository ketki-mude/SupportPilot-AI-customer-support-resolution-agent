"""Failure injection driven by MOCK_FAILURE_RATE."""

import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from main import CommerceStore, MockSettings
from tests.conftest import ClientFactory

REFUND = {"order_id": "ORD-10002", "amount": "29.98", "reason": "Wrong color"}


async def test_full_failure_rate_returns_503(make_client: ClientFactory) -> None:
    async with make_client(failure_rate=1.0, slow_fraction=0.0) as client:
        response = await client.get("/orders/ORD-10001")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["error"]["code"] == "service_unavailable"


async def test_injected_503_does_not_perform_the_action(
    make_client: ClientFactory, store: CommerceStore
) -> None:
    async with make_client(failure_rate=1.0, slow_fraction=0.0) as client:
        response = await client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "f1"})
    assert response.status_code == 503
    assert store.orders["ORD-10002"].refunded_amount == 0
    assert "refunds:f1" not in store.idempotency


async def test_health_is_never_faulted(make_client: ClientFactory) -> None:
    async with make_client(failure_rate=1.0, slow_fraction=0.0) as client:
        response = await client.get("/health")
    assert response.status_code == 200


async def test_slow_fault_delays_then_succeeds(make_client: ClientFactory) -> None:
    async with make_client(failure_rate=1.0, slow_fraction=1.0, slow_seconds=0.2) as client:
        started = time.perf_counter()
        response = await client.get("/orders/ORD-10001")
        elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert elapsed >= 0.2


async def test_zero_failure_rate_never_faults(make_client: ClientFactory) -> None:
    async with make_client(failure_rate=0.0) as client:
        statuses = {(await client.get("/orders/ORD-10001")).status_code for _ in range(50)}
    assert statuses == {200}


async def test_partial_failure_rate_mixes_errors_and_successes(
    make_client: ClientFactory,
) -> None:
    async with make_client(failure_rate=0.5, slow_fraction=0.0, random_seed=42) as client:
        statuses = [(await client.get("/orders/ORD-10001")).status_code for _ in range(200)]
    failures = statuses.count(503)
    assert set(statuses) == {200, 503}
    assert 60 < failures < 140


async def test_retry_after_503_with_same_key_refunds_once(
    make_client: ClientFactory, store: CommerceStore
) -> None:
    headers = {"Idempotency-Key": "retry-me"}
    async with make_client(failure_rate=0.5, slow_fraction=0.0, random_seed=1) as client:
        responses = [await client.post("/refunds", json=REFUND, headers=headers) for _ in range(10)]
    succeeded = [r for r in responses if r.status_code == 201]
    assert succeeded
    assert {r.status_code for r in responses} <= {201, 503}
    assert len({r.json()["refund_id"] for r in succeeded}) == 1
    assert store.orders["ORD-10002"].refunded_amount == Decimal("29.98")


def test_failure_rate_is_bounded() -> None:
    with pytest.raises(ValidationError):
        MockSettings(failure_rate=1.5)


def test_failure_rate_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_FAILURE_RATE", "0.25")
    assert MockSettings().failure_rate == 0.25
