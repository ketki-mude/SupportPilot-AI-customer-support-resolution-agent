"""Access-token expiry and the rotating refresh cookie."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import Role
from app.db.models import RefreshToken, User
from tests.agent_support import Api
from tests.conftest import TEST_PASSWORD

Sessions = async_sessionmaker[AsyncSession]
COOKIE = "sp_refresh"


async def _login(api: Api, who: str = "customer") -> Response:
    response = await api.client.post(
        "/api/auth/login", json={"email": f"{who}@acme.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response


async def _refresh(api: Api, cookie: str | None) -> Response:
    """Refresh as a browser holding exactly this cookie."""
    api.client.cookies.clear()
    if cookie:
        api.client.cookies.set(COOKIE, cookie)
    return await api.client.post("/api/auth/refresh")


def _settings(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


async def test_login_sets_a_locked_down_refresh_cookie(api: Api) -> None:
    response = await _login(api)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    for flag in ("HttpOnly", "SameSite=strict", "Path=/api/auth", "Max-Age=604800"):
        assert flag in cookie
    assert "Secure" not in cookie  # local http; production sets it
    assert response.json()["expires_in"] == get_settings().access_token_expire_minutes * 60


async def test_cookies_are_secure_in_production(api: Api) -> None:
    api.app.dependency_overrides[get_settings] = lambda: _settings(app_env="production")
    assert "Secure" in (await _login(api)).headers["set-cookie"]


async def test_refresh_rotates_the_cookie_and_issues_a_working_token(api: Api) -> None:
    first = (await _login(api)).cookies[COOKIE]
    response = await _refresh(api, first)
    assert response.status_code == 200
    rotated = response.cookies[COOKIE]
    assert rotated and rotated != first
    me = await api.client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    assert me.json()["email"] == "customer@acme.com"


async def test_two_tabs_refreshing_together_both_succeed(api: Api) -> None:
    first = (await _login(api)).cookies[COOKIE]
    assert (await _refresh(api, first)).status_code == 200
    # The other tab still sent the old cookie a moment later: inside the grace window.
    late = await _refresh(api, first)
    assert late.status_code == 200
    assert COOKIE not in late.cookies  # keeps the cookie the first tab already received


async def test_reusing_a_rotated_token_revokes_every_session(
    api: Api, live_sessions: Sessions
) -> None:
    api.app.dependency_overrides[get_settings] = lambda: _settings(refresh_reuse_grace_seconds=0)
    stolen = (await _login(api)).cookies[COOKIE]
    current = (await _refresh(api, stolen)).cookies[COOKIE]

    replay = await _refresh(api, stolen)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_refresh_token"
    assert (await _refresh(api, current)).status_code == 401  # the legitimate one is gone too
    async with live_sessions() as session:
        reasons = set(await session.scalars(select(RefreshToken.revoked_reason)))
    assert "reuse" in reasons


async def test_logout_revokes_and_clears_the_cookie(api: Api) -> None:
    token = (await _login(api)).cookies[COOKIE]
    api.client.cookies.clear()
    api.client.cookies.set(COOKIE, token)
    response = await api.client.post("/api/auth/logout")
    assert response.status_code == 204
    assert (
        f'{COOKIE}=""' in response.headers["set-cookie"]
        and "Max-Age=0" in response.headers["set-cookie"]
    )
    assert (await _refresh(api, token)).status_code == 401


async def test_refresh_without_a_cookie_or_with_garbage(api: Api) -> None:
    assert (await _refresh(api, None)).status_code == 401
    assert (await _refresh(api, "not-a-real-token")).status_code == 401


async def test_expired_refresh_token_is_rejected(api: Api, live_sessions: Sessions) -> None:
    token = (await _login(api)).cookies[COOKIE]
    async with live_sessions() as session:
        await session.execute(
            update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    assert (await _refresh(api, token)).status_code == 401


async def test_refresh_reflects_the_current_role(api: Api, live_sessions: Sessions) -> None:
    token = (await _login(api, "agent")).cookies[COOKIE]
    async with live_sessions() as session:
        await session.execute(
            update(User).where(User.email == "agent@acme.com").values(role=Role.SUPERVISOR)
        )
        await session.commit()
    access = (await _refresh(api, token)).json()["access_token"]
    assert jwt.decode(access, options={"verify_signature": False})["role"] == "supervisor"


@pytest.mark.parametrize("path", ["/api/auth/refresh", "/api/auth/logout"])
async def test_session_endpoints_never_crash_without_a_cookie(api: Api, path: str) -> None:
    api.client.cookies.clear()
    response = await api.client.post(path)
    assert response.status_code in {204, 401}
