"""Login and /me against the real database."""

import uuid

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import Role, create_access_token
from app.db.models import User
from tests.conftest import TEST_CUSTOMER_ID, TEST_PASSWORD


async def _login(client: AsyncClient, email: str, password: str = TEST_PASSWORD) -> Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})


@pytest.mark.parametrize("role", list(Role))
async def test_login_returns_token_and_role(
    client: AsyncClient, users: dict[Role, User], role: Role
) -> None:
    response = await _login(client, users[role].email)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] == get_settings().access_token_expire_minutes * 60
    assert body["user"]["role"] == role.value
    assert body["user"]["email"] == users[role].email
    assert "password_hash" not in body["user"]


async def test_customer_login_carries_customer_id(
    client: AsyncClient, users: dict[Role, User]
) -> None:
    response = await _login(client, users[Role.CUSTOMER].email)
    assert response.json()["user"]["customer_id"] == TEST_CUSTOMER_ID


async def test_email_is_case_insensitive(client: AsyncClient, users: dict[Role, User]) -> None:
    response = await _login(client, "AGENT@Acme.com")
    assert response.status_code == 200


async def test_wrong_password_and_unknown_email_look_identical(
    client: AsyncClient, users: dict[Role, User]
) -> None:
    wrong_password = await _login(client, users[Role.AGENT].email, "not-the-password")
    unknown_email = await _login(client, "nobody@acme.com")
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


async def test_login_validation(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"email": "not-an-email", "password": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("role", list(Role))
async def test_me_returns_the_token_owner(
    client: AsyncClient, users: dict[Role, User], role: Role
) -> None:
    token = (await _login(client, users[role].email)).json()["access_token"]
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["id"] == str(users[role].id)
    assert response.json()["role"] == role.value


async def test_me_without_token(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


async def test_me_with_garbage_token(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_token_for_deleted_user_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = create_access_token(uuid.uuid4(), Role.ADMIN, get_settings())
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_database_role_wins_over_token_role(
    client: AsyncClient, users: dict[Role, User], db_session: AsyncSession
) -> None:
    supervisor = users[Role.SUPERVISOR]
    token, _ = create_access_token(supervisor.id, Role.SUPERVISOR, get_settings())
    supervisor.role = Role.AGENT
    await db_session.flush()
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["role"] == "agent"
