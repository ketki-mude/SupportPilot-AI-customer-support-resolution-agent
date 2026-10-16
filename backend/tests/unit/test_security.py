"""Password hashing and access tokens."""

import uuid
from datetime import timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.security import (
    Role,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


@pytest.fixture
def settings() -> Settings:
    return get_settings()


def test_hash_and_verify_round_trip() -> None:
    hashed = hash_password("s3cret-pass", rounds=4)
    assert hashed != "s3cret-pass"
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong-pass", hashed)


def test_hashes_are_salted() -> None:
    assert hash_password("same", rounds=4) != hash_password("same", rounds=4)


def test_hash_rejects_passwords_bcrypt_would_truncate() -> None:
    with pytest.raises(ValueError, match="72 bytes"):
        hash_password("x" * 73, rounds=4)


def test_verify_handles_overlong_and_malformed_input() -> None:
    hashed = hash_password("short", rounds=4)
    assert not verify_password("x" * 200, hashed)
    assert not verify_password("short", "not-a-bcrypt-hash")


def test_token_round_trip(settings: Settings) -> None:
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id, Role.SUPERVISOR, settings)
    claims = decode_access_token(token, settings)
    assert claims.user_id == user_id
    assert claims.role is Role.SUPERVISOR
    assert expires_in == settings.access_token_expire_minutes * 60


def test_expired_token_is_rejected(settings: Settings) -> None:
    token, _ = create_access_token(
        uuid.uuid4(), Role.AGENT, settings, expires_delta=timedelta(seconds=-1)
    )
    with pytest.raises(TokenError):
        decode_access_token(token, settings)


def test_token_signed_with_another_secret_is_rejected(settings: Settings) -> None:
    other = settings.model_copy(update={"jwt_secret": SecretStr("z" * 40)})
    token, _ = create_access_token(uuid.uuid4(), Role.ADMIN, other)
    with pytest.raises(TokenError):
        decode_access_token(token, settings)


def test_tampered_role_is_rejected(settings: Settings) -> None:
    token, _ = create_access_token(uuid.uuid4(), Role.CUSTOMER, settings)
    header, _, signature = token.split(".")
    forged_payload = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": "admin", "type": "access", "iat": 0, "exp": 9e9},
        "attacker-key-that-is-long-enough-to-sign-with",
        "HS256",
    ).split(".")[1]
    with pytest.raises(TokenError):
        decode_access_token(f"{header}.{forged_payload}.{signature}", settings)


def test_unsigned_token_is_rejected(settings: Settings) -> None:
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": "admin", "type": "access", "iat": 0, "exp": 9e9},
        key=None,
        algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_access_token(token, settings)


@pytest.mark.parametrize(
    "claims",
    [
        {"role": "admin", "type": "access"},
        {"sub": "not-a-uuid", "role": "admin", "type": "access"},
        {"sub": str(uuid.uuid4()), "role": "superuser", "type": "access"},
        {"sub": str(uuid.uuid4()), "role": "admin", "type": "refresh"},
    ],
)
def test_malformed_claims_are_rejected(settings: Settings, claims: dict[str, str]) -> None:
    token = jwt.encode(
        claims | {"iat": 0, "exp": 9e9}, settings.jwt_secret.get_secret_value(), "HS256"
    )
    with pytest.raises(TokenError):
        decode_access_token(token, settings)
