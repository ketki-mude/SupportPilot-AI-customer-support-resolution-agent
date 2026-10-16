"""Customer and order lookups, and the shape of the seed data."""

from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient

from main import CommerceStore, OrderStatus, RefundStatus
from tests.conftest import NOW


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_get_customer(client: AsyncClient) -> None:
    response = await client.get("/customers/cus_001")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Jordan Lee"
    assert body["tier"] == "gold"
    assert body["fraud_flag"] is False


async def test_unknown_customer_returns_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/customers/cus_999")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "customer_not_found", "message": "Customer not found"}
    }


async def test_list_customer_orders(client: AsyncClient) -> None:
    response = await client.get("/customers/cus_001/orders")
    assert response.status_code == 200
    orders = response.json()
    assert len(orders) == 8
    assert {o["customer_id"] for o in orders} == {"cus_001"}


async def test_orders_for_unknown_customer_is_404(client: AsyncClient) -> None:
    response = await client.get("/customers/cus_999/orders")
    assert response.status_code == 404


async def test_get_order_fields(client: AsyncClient) -> None:
    response = await client.get("/orders/ORD-10001")
    assert response.status_code == 200
    order = response.json()
    assert order["status"] == "delivered"
    assert order["total"] == "79.99"
    assert order["payment_method_last4"] == "4242"
    assert order["items"][0]["name"] == "Aurora ANC wireless headphones"
    assert order["delivered_at"].startswith((NOW - timedelta(days=4)).date().isoformat())
    assert order["refund_status"] == "none"


async def test_unknown_order_is_404(client: AsyncClient) -> None:
    response = await client.get("/orders/ORD-00000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "order_not_found"


def test_seed_has_ten_customers_and_thirty_orders(store: CommerceStore) -> None:
    assert len(store.customers) == 10
    assert len(store.orders) == 30
    assert {o.customer_id for o in store.orders.values()} == set(store.customers)


def test_seed_order_totals_match_items(store: CommerceStore) -> None:
    for order in store.orders.values():
        assert order.total == sum(i.unit_price * i.quantity for i in order.items)


def test_seed_covers_edge_cases(store: CommerceStore) -> None:
    orders = store.orders
    assert orders["ORD-10004"].delivered_at == NOW - timedelta(days=45)
    assert orders["ORD-10005"].refund_status == RefundStatus.FULL
    assert orders["ORD-10026"].refund_status == RefundStatus.PARTIAL
    assert orders["ORD-10006"].total > Decimal(500)
    assert orders["ORD-10003"].status == OrderStatus.IN_TRANSIT
    assert orders["ORD-10003"].delivered_at is None
    assert orders["ORD-10009"].customer_id != "cus_001"  # wrong-owner claim target
    assert store.customers["cus_007"].fraud_flag is True


def test_seed_orders_have_consistent_dates(store: CommerceStore) -> None:
    for order in store.orders.values():
        if order.status == OrderStatus.DELIVERED:
            assert order.shipped_at is not None
            assert order.delivered_at is not None
            assert order.placed_at <= order.shipped_at <= order.delivered_at
        else:
            assert order.delivered_at is None
        if order.status == OrderStatus.IN_TRANSIT:
            assert order.estimated_delivery_at is not None
            assert order.estimated_delivery_at > NOW
