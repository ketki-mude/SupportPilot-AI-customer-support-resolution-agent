"""Every failure renders as {"error": {"code", "message"}}."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.errors import ConflictError, NotFoundError, register_exception_handlers


class Payload(BaseModel):
    password: str
    count: int


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("Ticket not found")

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("Already decided", code="approval_already_decided")

    @app.post("/validate")
    async def validate(body: Payload) -> Payload:
        return body

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    return app


def _client() -> AsyncClient:
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_app_error() -> None:
    async with _client() as client:
        response = await client.get("/missing")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Ticket not found"}}


async def test_app_error_with_custom_code() -> None:
    async with _client() as client:
        response = await client.get("/conflict")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_already_decided"


async def test_unknown_route_uses_envelope() -> None:
    async with _client() as client:
        response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_method_not_allowed_uses_envelope() -> None:
    async with _client() as client:
        response = await client.delete("/missing")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_validation_error_lists_fields_without_echoing_input() -> None:
    async with _client() as client:
        response = await client.post("/validate", json={"password": "hunter2", "count": "many"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "count"]
    assert "hunter2" not in response.text


async def test_unexpected_error_hides_internals() -> None:
    async with _client() as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred"}
    }
    assert "hunter2" not in response.text
