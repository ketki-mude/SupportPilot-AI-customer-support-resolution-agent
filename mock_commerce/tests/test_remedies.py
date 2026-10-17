"""One remedy (refund or replacement) per order."""

import pytest
from httpx import AsyncClient

from main import CommerceStore, Remedy


def _refund(order_id: str, amount: str) -> dict[str, str]:
    return {"order_id": order_id, "amount": amount, "reason": "Arrived damaged"}


def _replacement(order_id: str) -> dict[str, str]:
    return {"order_id": order_id, "reason": "Arrived damaged"}


async def test_refund_records_the_remedy(client: AsyncClient, store: CommerceStore) -> None:
    await client.post(
        "/refunds", json=_refund("ORD-10002", "29.98"), headers={"Idempotency-Key": "a"}
    )
    assert store.orders["ORD-10002"].remedy == Remedy.REFUND
    order = (await client.get("/orders/ORD-10002")).json()
    assert order["remedy"] == "refund"


async def test_second_refund_with_new_key_is_rejected(
    client: AsyncClient, store: CommerceStore
) -> None:
    await client.post(
        "/refunds", json=_refund("ORD-10002", "9.99"), headers={"Idempotency-Key": "a"}
    )
    response = await client.post(
        "/refunds", json=_refund("ORD-10002", "9.99"), headers={"Idempotency-Key": "b"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "remedy_already_applied"
    assert len([r for r in store.refunds.values() if r.order_id == "ORD-10002"]) == 1


async def test_replay_after_remedy_still_returns_original_result(client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "a"}
    first = await client.post("/refunds", json=_refund("ORD-10002", "29.98"), headers=headers)
    replay = await client.post("/refunds", json=_refund("ORD-10002", "29.98"), headers=headers)
    assert replay.status_code == 201
    assert replay.json() == first.json()


async def test_refund_then_replacement_is_rejected(client: AsyncClient) -> None:
    await client.post(
        "/refunds", json=_refund("ORD-10002", "29.98"), headers={"Idempotency-Key": "a"}
    )
    response = await client.post(
        "/replacements", json=_replacement("ORD-10002"), headers={"Idempotency-Key": "b"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "remedy_already_applied"


async def test_replacement_then_refund_is_rejected(
    client: AsyncClient, store: CommerceStore
) -> None:
    await client.post(
        "/replacements", json=_replacement("ORD-10002"), headers={"Idempotency-Key": "a"}
    )
    response = await client.post(
        "/refunds", json=_refund("ORD-10002", "29.98"), headers={"Idempotency-Key": "b"}
    )
    assert response.status_code == 409
    assert store.orders["ORD-10002"].refunded_amount == 0


@pytest.mark.parametrize("order_id", ["ORD-10005", "ORD-10026", "ORD-10016"])
async def test_seeded_remedies_block_new_ones(client: AsyncClient, order_id: str) -> None:
    refund = await client.post(
        "/refunds", json=_refund(order_id, "1.00"), headers={"Idempotency-Key": "r"}
    )
    replacement = await client.post(
        "/replacements", json=_replacement(order_id), headers={"Idempotency-Key": "p"}
    )
    assert refund.status_code == replacement.status_code == 409
    assert refund.json()["error"]["code"] == "remedy_already_applied"
    assert replacement.json()["error"]["code"] == "remedy_already_applied"
