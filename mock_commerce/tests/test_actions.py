"""Refunds and replacements, including idempotency guarantees."""

import asyncio
from decimal import Decimal

from httpx import AsyncClient

from main import CommerceStore

REFUND = {"order_id": "ORD-10001", "amount": "79.99", "reason": "Arrived damaged"}
REPLACEMENT = {"order_id": "ORD-10001", "reason": "Arrived damaged"}


async def test_refund_succeeds_and_updates_order(client: AsyncClient, store: CommerceStore) -> None:
    response = await client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "k1"})
    assert response.status_code == 201
    refund = response.json()
    assert refund["status"] == "succeeded"
    assert refund["amount"] == "79.99"
    assert refund["refund_id"].startswith("re_")
    assert "Idempotent-Replayed" not in response.headers

    order = (await client.get("/orders/ORD-10001")).json()
    assert order["refunded_amount"] == "79.99"
    assert order["refund_status"] == "full"


async def test_same_key_replays_original_result_without_duplicating(
    client: AsyncClient, store: CommerceStore
) -> None:
    headers = {"Idempotency-Key": "refund-TCK-1000"}
    first = await client.post("/refunds", json=REFUND, headers=headers)
    second = await client.post("/refunds", json=REFUND, headers=headers)

    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert len([r for r in store.refunds.values() if r.order_id == "ORD-10001"]) == 1
    assert store.orders["ORD-10001"].refunded_amount == Decimal("79.99")


async def test_concurrent_requests_with_same_key_refund_once(
    client: AsyncClient, store: CommerceStore
) -> None:
    headers = {"Idempotency-Key": "double-click"}
    responses = await asyncio.gather(
        *(client.post("/refunds", json=REFUND, headers=headers) for _ in range(5))
    )
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["refund_id"] for r in responses}) == 1
    assert store.orders["ORD-10001"].refunded_amount == Decimal("79.99")


async def test_same_key_with_different_body_is_rejected(
    client: AsyncClient, store: CommerceStore
) -> None:
    headers = {"Idempotency-Key": "k2"}
    await client.post("/refunds", json=REFUND, headers=headers)
    response = await client.post("/refunds", json=REFUND | {"amount": "10.00"}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert store.orders["ORD-10001"].refunded_amount == Decimal("79.99")


async def test_refund_requires_idempotency_key(client: AsyncClient) -> None:
    response = await client.post("/refunds", json=REFUND)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


async def test_refund_cannot_exceed_refundable_balance(client: AsyncClient) -> None:
    # ORD-10026 is 449.00 with 50.00 already refunded.
    response = await client.post(
        "/refunds",
        json={"order_id": "ORD-10026", "amount": "400.00", "reason": "Broken"},
        headers={"Idempotency-Key": "k3"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "refund_exceeds_balance"


async def test_cancelled_order_has_nothing_to_refund(client: AsyncClient) -> None:
    response = await client.post(
        "/refunds",
        json={"order_id": "ORD-10021", "amount": "1.00", "reason": "Cancelled"},
        headers={"Idempotency-Key": "k4"},
    )
    assert response.status_code == 422


async def test_failed_refund_is_not_stored_against_the_key(client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "k5"}
    body = {"order_id": "ORD-10026", "amount": "400.00", "reason": "Broken"}
    assert (await client.post("/refunds", json=body, headers=headers)).status_code == 422
    assert (await client.post("/refunds", json=body, headers=headers)).status_code == 422


async def test_refund_validation(client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "k6"}
    for bad in (
        REFUND | {"amount": "0"},
        REFUND | {"amount": "-5"},
        REFUND | {"amount": "1.001"},
        REFUND | {"reason": ""},
        REFUND | {"unexpected": "field"},
    ):
        response = await client.post("/refunds", json=bad, headers=headers)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "validation_error"


async def test_refund_unknown_order(client: AsyncClient) -> None:
    response = await client.post(
        "/refunds", json=REFUND | {"order_id": "ORD-00000"}, headers={"Idempotency-Key": "k7"}
    )
    assert response.status_code == 404


async def test_replacement_is_idempotent(client: AsyncClient, store: CommerceStore) -> None:
    headers = {"Idempotency-Key": "replace-TCK-1000"}
    first = await client.post("/replacements", json=REPLACEMENT, headers=headers)
    second = await client.post("/replacements", json=REPLACEMENT, headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert first.json()["items"][0]["sku"] == "SKU-HP-450"
    assert len(store.replacements) == 1


async def test_refund_and_replacement_keys_are_separate(
    client: AsyncClient, store: CommerceStore
) -> None:
    headers = {"Idempotency-Key": "shared"}
    refund = await client.post("/refunds", json=REFUND, headers=headers)
    replacement = await client.post("/replacements", json=REPLACEMENT, headers=headers)
    assert refund.status_code == replacement.status_code == 201
    assert len(store.refunds) == 4  # three seeded plus this one
    assert len(store.replacements) == 1


async def test_replacement_requires_delivered_order(client: AsyncClient) -> None:
    response = await client.post(
        "/replacements",
        json={"order_id": "ORD-10003", "reason": "Late"},
        headers={"Idempotency-Key": "k8"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "order_not_delivered"


async def test_replacement_rejects_unknown_sku(client: AsyncClient) -> None:
    response = await client.post(
        "/replacements",
        json=REPLACEMENT | {"skus": ["SKU-NOPE"]},
        headers={"Idempotency-Key": "k9"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_sku"


async def test_replacement_requires_idempotency_key(client: AsyncClient) -> None:
    response = await client.post("/replacements", json=REPLACEMENT)
    assert response.status_code == 400
