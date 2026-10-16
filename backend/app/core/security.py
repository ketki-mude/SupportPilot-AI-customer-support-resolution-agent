"""Password hashing, JWT access tokens, and the role model."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import lru_cache

import bcrypt
import jwt

from app.core.config import Settings

BCRYPT_MAX_BYTES = 72
TOKEN_TYPE_ACCESS = "access"  # noqa: S105  # a claim value, not a credential


class Role(StrEnum):
    """User roles. Staff roles are agent, supervisor, and admin."""

    CUSTOMER = "customer"
    AGENT = "agent"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


class TokenError(Exception):
    """Raised when a token is missing claims, expired, or fails verification."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """The verified contents of an access token."""

    user_id: uuid.UUID
    role: Role
    expires_at: datetime


def hash_password(password: str, *, rounds: int) -> str:
    """Hash a password with bcrypt. Rejects passwords bcrypt would silently truncate."""
    raw = password.encode()
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a bcrypt hash without raising on bad input."""
    raw = password.encode()
    if len(raw) > BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode())
    except ValueError:
        return False


@lru_cache
def dummy_password_hash(rounds: int) -> str:
    """A throwaway hash so unknown-email logins cost the same time as wrong passwords."""
    return hash_password("timing-equalizer", rounds=rounds)


def create_access_token(
    user_id: uuid.UUID,
    role: Role,
    settings: Settings,
    *,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    """Sign an access token and return it with its lifetime in seconds."""
    lifetime = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    issued_at = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role.value,
        "type": TOKEN_TYPE_ACCESS,
        "iat": issued_at,
        "exp": issued_at + lifetime,
    }
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    """Verify an access token's signature, expiry, and claims."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "role", "type", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload["type"] != TOKEN_TYPE_ACCESS:
        raise TokenError("Not an access token")
    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            role=Role(payload["role"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except ValueError as exc:
        raise TokenError("Malformed token claims") from exc
