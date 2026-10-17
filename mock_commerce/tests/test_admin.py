"""Evaluation-only admin endpoints: load a case's data and faults, read back the effects."""

from httpx import AsyncClient

from tests.conftest import ClientFactory

FIXTURE = {
    "customers": [
        {
            "id": "cus_e1",
            "name": "Eval Customer",
            "email": "eval@example.com",
            "phone": "+1-555-0199",
            "tier": "standard",
            "fraud_flag": False,
            "member_since_days_ago": 100,
        }
    ],
    "orders": [
        {
            "order_id": "ORD-90001",
            "customer_id": "cus_e1",
            "status": "delivered",
            "placed_days_ago": 6,
            "shipped_days_ago": 4,
            "delivered_days_ago": 2,
            "payment_method_last4": "1111",
            "items": [{"sku": "SKU-E1", "name": "Kettle", "quantity": 1, "unit_price": "24.00"}],
        }
    ],
}


async def test_admin_is_off_by_default(client: AsyncClient) -> None:
    assert (await client.post("/admin/reset", json={"fixture": FIXTURE})).status_code == 404
    assert (await client.get("/admin/state")).status_code == 404


async def test_reset_replaces_all_data(make_client: ClientFactory) -> None:
    async with make_client(admin_enabled=True) as client:
        assert (await client.post("/admin/reset", json={"fixture": FIXTURE})).status_code == 204
        assert (await client.get("/orders/ORD-90001")).json()["total"] == "24.00"
        assert (await client.get("/orders/ORD-10001")).status_code == 404  # seed data is gone


async def test_state_reports_what_happened(make_client: ClientFactory) -> None:
    async with make_client(admin_enabled=True) as client:
        await client.post("/admin/reset", json={"fixture": FIXTURE})
        await client.post(
            "/refunds",
            json={"order_id": "ORD-90001", "amount": "24.00", "reason": "Broken"},
            headers={"Idempotency-Key": "k"},
        )
        state = (await client.get("/admin/state")).json()
    assert [(r["order_id"], r["amount"]) for r in state["refunds"]] == [("ORD-90001", "24.00")]
    assert state["replacements"] == []
    assert state["orders"][0]["remedy"] == "refund"


async def test_reset_sets_faults_but_admin_calls_are_never_faulted(
    make_client: ClientFactory,
) -> None:
    async with make_client(admin_enabled=True) as client:
        reset = await client.post(
            "/admin/reset",
            json={"fixture": FIXTURE, "faults": {"failure_rate": 1.0, "slow_fraction": 0.0}},
        )
        assert reset.status_code == 204
        assert (await client.get("/orders/ORD-90001")).status_code == 503
        assert (await client.get("/admin/state")).status_code == 200
        # A later reset clears the faults again.
        await client.post("/admin/reset", json={"fixture": FIXTURE})
        assert (await client.get("/orders/ORD-90001")).status_code == 200


async def test_reset_rejects_unknown_fields(make_client: ClientFactory) -> None:
    async with make_client(admin_enabled=True) as client:
        response = await client.post("/admin/reset", json={"fixture": FIXTURE, "extra": 1})
    assert response.status_code == 422
