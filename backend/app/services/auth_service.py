"""Refresh token issue, rotation, and revocation.

Each refresh revokes the presented token and issues a new one. Presenting an already-rotated
token again means it was copied: every session of that user is revoked. A short grace window
covers two tabs refreshing at the same moment.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.db.models import RefreshToken, User

logger = structlog.get_logger(__name__)

REFRESH_COOKIE = "sp_refresh"
ROTATED = "rotated"
LOGGED_OUT = "logout"
REUSED = "reuse"


class InvalidRefreshTokenError(UnauthorizedError):
    code = "invalid_refresh_token"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID, settings: Settings) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash(token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    await session.flush()
    return token


async def rotate_refresh_token(
    session: AsyncSession, token: str, settings: Settings
) -> tuple[User, str | None]:
    """Exchange a refresh token for its user and a new token.

    Returns no new token when a just-rotated token is presented within the grace window; the
    caller then issues an access token only and leaves the cookie the other tab already set.
    """
    now = datetime.now(UTC)
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _hash(token)).with_for_update()
    )
    if row is None or row.expires_at <= now:
        raise InvalidRefreshTokenError("Your session has expired. Please sign in again.")
    user = await session.get(User, row.user_id)
    if user is None:
        raise InvalidRefreshTokenError("Your session has expired. Please sign in again.")

    if row.revoked_at is not None:
        in_grace = row.revoked_reason == ROTATED and now - row.revoked_at <= timedelta(
            seconds=settings.refresh_reuse_grace_seconds
        )
        if in_grace:
            return user, None
        await revoke_all(session, user.id, reason=REUSED)
        # Commit the revocation even though the request fails: it is the point of the check.
        await session.commit()
        logger.warning("refresh_token_reuse_detected", user_id=str(user.id))
        raise InvalidRefreshTokenError("Your session has expired. Please sign in again.")

    row.revoked_at = now
    row.revoked_reason = ROTATED
    return user, await issue_refresh_token(session, user.id, settings)


async def revoke_refresh_token(session: AsyncSession, token: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(token), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=LOGGED_OUT)
    )


async def revoke_all(session: AsyncSession, user_id: uuid.UUID, *, reason: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
