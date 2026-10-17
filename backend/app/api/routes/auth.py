"""Login, session refresh, logout, and the current user."""

import asyncio
import math
from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.config import Settings
from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.security import create_access_token, dummy_password_hash, verify_password
from app.db.models import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.errors import ErrorResponse
from app.schemas.user import UserRead
from app.services import auth_service
from app.services.auth_service import REFRESH_COOKIE

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_PATH = "/api/auth"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """HttpOnly so scripts cannot read it; SameSite=Strict so other sites cannot send it."""
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _token_response(user: User, settings: Settings) -> TokenResponse:
    token, expires_in = create_access_token(user.id, user.role, settings)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserRead.model_validate(user)
    )


@router.post("/login", responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}})
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    """Exchange email and password for an access token and a refresh cookie."""
    wait = request.app.state.rate_limits.login.hit(body.email)
    if wait is not None:
        raise RateLimitedError(
            "Too many sign-in attempts. Please wait a minute and try again.",
            headers={"Retry-After": str(math.ceil(wait))},
        )
    user = await session.scalar(select(User).where(User.email == body.email))
    stored_hash = user.password_hash if user else dummy_password_hash(settings.bcrypt_rounds)
    # bcrypt is CPU-bound; keep it off the event loop.
    password_ok = await asyncio.to_thread(verify_password, body.password, stored_hash)
    if user is None or not password_ok:
        raise UnauthorizedError("Incorrect email or password", code="invalid_credentials")
    refresh_token = await auth_service.issue_refresh_token(session, user.id, settings)
    await session.commit()
    _set_refresh_cookie(response, refresh_token, settings)
    return _token_response(user, settings)


@router.post("/refresh", responses={401: {"model": ErrorResponse}})
async def refresh(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> TokenResponse:
    """A new access token for the refresh cookie, which is rotated on every use."""
    if not refresh_token:
        raise UnauthorizedError("Please sign in.", code="invalid_refresh_token")
    user, rotated = await auth_service.rotate_refresh_token(session, refresh_token, settings)
    await session.commit()
    if rotated is not None:
        _set_refresh_cookie(response, rotated, settings)
    return _token_response(user, settings)


@router.post("/logout", status_code=204)
async def logout(
    session: SessionDep, settings: SettingsDep, refresh_token: RefreshCookie = None
) -> Response:
    """Revoke the refresh token and clear the cookie. Access tokens expire on their own."""
    if refresh_token:
        await auth_service.revoke_refresh_token(session, refresh_token)
        await session.commit()
    response = Response(status_code=204)
    response.delete_cookie(
        REFRESH_COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
    )
    return response


@router.get("/me", responses={401: {"model": ErrorResponse}})
async def me(user: CurrentUser) -> UserRead:
    """Return the authenticated user."""
    return UserRead.model_validate(user)
