"""Cancelling processing orders."""

import asyncio
from decimal import Decimal

import pytest
from httpx import AsyncClient, Response

from main import CommerceStore, OrderStatus, PaymentStatus

REASON = {"reason": "Ordered by mistake"}


async def _cancel(client: AsyncClient, order_id: str, key: str = "c1", **body: str) -> Response:
    return await client.post(
        f"/orders/{order_id}/cancel", json=REASON | body, headers={"Idempotency-Key": key}
    )


async def test_cancel_captured_order_refunds_in_the_same_call(
    client: AsyncClient, store: CommerceStore
) -> None:
    response = await _cancel(client, "ORD-10007")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["payment_action"] == "refunded"
    assert body["refund"]["amount"] == "45.00"

    order = store.orders["ORD-10007"]
    assert order.status == OrderStatus.CANCELLED
    assert order.cancelled_at is not None
    assert order.refunded_amount == Decimal("45.00")
    assert order.remedy is None  # a cancellation refund is not a remedy


async def test_cancel_authorized_order_voids_the_payment(
    client: AsyncClient, store: CommerceStore
) -> None:
    response = await _cancel(client, "ORD-10011")
    assert response.status_code == 201
    assert response.json()["payment_action"] == "voided"
    assert response.json()["refund"] is None
    order = store.orders["ORD-10011"]
    assert order.payment_status == PaymentStatus.VOIDED
    assert order.refunded_amount == 0


@pytest.mark.parametrize(
    ("order_id", "status"),
    [
        ("ORD-10015", "shipped"),
        ("ORD-10003", "in_transit"),
        ("ORD-10001", "delivered"),
        ("ORD-10021", "cancelled"),
    ],
)
async def test_only_processing_orders_can_be_cancelled(
    client: AsyncClient, order_id: str, status: str
) -> None:
    response = await _cancel(client, order_id)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "order_not_cancellable"
    assert status in error["message"]


async def test_cancel_is_idempotent(client: AsyncClient, store: CommerceStore) -> None:
    first = await _cancel(client, "ORD-10007", key="cancel-TCK-1000")
    replay = await _cancel(client, "ORD-10007", key="cancel-TCK-1000")
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()
    assert len([r for r in store.refunds.values() if r.order_id == "ORD-10007"]) == 1


async def test_concurrent_cancels_with_same_key_refund_once(
    client: AsyncClient, store: CommerceStore
) -> None:
    responses = await asyncio.gather(*(_cancel(client, "ORD-10007", key="k") for _ in range(5)))
    assert {r.status_code for r in responses} == {201}
    assert store.orders["ORD-10007"].refunded_amount == Decimal("45.00")


async def test_same_key_for_another_order_is_rejected(client: AsyncClient) -> None:
    await _cancel(client, "ORD-10007", key="shared")
    response = await _cancel(client, "ORD-10011", key="shared")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"


async def test_same_key_with_different_reason_is_rejected(client: AsyncClient) -> None:
    await _cancel(client, "ORD-10007", key="k")
    response = await _cancel(client, "ORD-10007", key="k", reason="Found it cheaper")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"


async def test_second_cancel_with_new_key_is_rejected(client: AsyncClient) -> None:
    await _cancel(client, "ORD-10007", key="k1")
    response = await _cancel(client, "ORD-10007", key="k2")
    assert response.status_code == 409


async def test_cancelled_order_cannot_also_be_refunded(client: AsyncClient) -> None:
    await _cancel(client, "ORD-10007")
    response = await client.post(
        "/refunds",
        json={"order_id": "ORD-10007", "amount": "45.00", "reason": "Cancelled"},
        headers={"Idempotency-Key": "r"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "refund_exceeds_balance"


async def test_cancel_requires_idempotency_key(client: AsyncClient) -> None:
    response = await client.post("/orders/ORD-10007/cancel", json=REASON)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


async def test_cancel_unknown_order(client: AsyncClient) -> None:
    response = await _cancel(client, "ORD-00000")
    assert response.status_code == 404


async def test_cancel_requires_a_reason(client: AsyncClient) -> None:
    response = await _cancel(client, "ORD-10007", reason="")
    assert response.status_code == 422
