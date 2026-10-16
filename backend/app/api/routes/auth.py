"""Login and current-user endpoints."""

import asyncio

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.errors import UnauthorizedError
from app.core.security import create_access_token, dummy_password_hash, verify_password
from app.db.models import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.errors import ErrorResponse
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", responses={401: {"model": ErrorResponse}})
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    """Exchange email and password for a bearer access token."""
    user = await session.scalar(select(User).where(User.email == body.email))
    stored_hash = user.password_hash if user else dummy_password_hash(settings.bcrypt_rounds)
    # bcrypt is CPU-bound; keep it off the event loop.
    password_ok = await asyncio.to_thread(verify_password, body.password, stored_hash)
    if user is None or not password_ok:
        raise UnauthorizedError("Incorrect email or password", code="invalid_credentials")
    token, expires_in = create_access_token(user.id, user.role, settings)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserRead.model_validate(user)
    )


@router.get("/me", responses={401: {"model": ErrorResponse}})
async def me(user: CurrentUser) -> UserRead:
    """Return the authenticated user."""
    return UserRead.model_validate(user)
