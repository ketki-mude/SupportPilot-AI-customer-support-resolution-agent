"""Health endpoint."""

from collections.abc import AsyncIterator
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_session
from app.main import create_app


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


class _BrokenSession:
    async def execute(self, *_: Any) -> None:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError())


async def test_health_reports_database_outage() -> None:
    app = create_app()

    async def broken() -> AsyncIterator[_BrokenSession]:
        yield _BrokenSession()

    app.dependency_overrides[get_session] = broken
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "unavailable",
        "version": "0.1.0",
        "commerce_circuit": None,
    }


async def test_openapi_docs_are_served(client: AsyncClient) -> None:
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    assert "/api/auth/login" in response.json()["paths"]
