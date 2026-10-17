"""Settings validation."""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.rate_limit import RateLimits

BASE: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "jwt_secret": "s" * 32,
}


THRESHOLD_ENV = (
    "REFUND_WINDOW_DAYS",
    "AUTO_APPROVE_MAX_USD",
    "SUPERVISOR_APPROVAL_MAX_USD",
    "MIN_CONFIDENCE",
)


@pytest.fixture(autouse=True)
def _clear_threshold_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in THRESHOLD_ENV:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **(BASE | overrides))  # type: ignore[call-arg]


def test_default_thresholds_match_business_rules() -> None:
    settings = _settings()
    assert settings.refund_window_days == 30
    assert settings.auto_approve_max_usd == Decimal(50)
    assert settings.supervisor_approval_max_usd == Decimal(500)
    assert settings.min_confidence == 0.6


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        _settings(jwt_secret="too-short")


def test_auto_approve_must_be_below_supervisor_threshold() -> None:
    with pytest.raises(ValidationError, match="AUTO_APPROVE_MAX_USD"):
        _settings(auto_approve_max_usd="600", supervisor_approval_max_usd="500")


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _settings(min_confidence=1.5)


def test_thresholds_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_APPROVE_MAX_USD", "25")
    monkeypatch.setenv("REFUND_WINDOW_DAYS", "14")
    settings = _settings()
    assert settings.auto_approve_max_usd == Decimal(25)
    assert settings.refund_window_days == 14


def test_secrets_are_masked_in_repr() -> None:
    settings = _settings(google_api_key="AIza-very-secret")
    assert "AIza-very-secret" not in repr(settings)
    assert "s" * 32 not in repr(settings)


def test_sessions_default_to_short_access_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    settings = _settings()
    assert settings.access_token_expire_minutes == 15
    assert settings.refresh_token_expire_days == 7


def test_cors_origins_are_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example, https://admin.example,")
    assert _settings().cors_origins == ["https://app.example", "https://admin.example"]


@pytest.mark.parametrize(
    ("env", "override", "secure"),
    [("development", None, False), ("production", None, True), ("development", True, True)],
)
def test_secure_cookies_follow_the_environment(
    env: str, override: bool | None, secure: bool
) -> None:
    assert _settings(app_env=env, cookie_secure=override).secure_cookies is secure


def test_rate_limits_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_MINUTE", "4")
    limits = RateLimits.from_settings(_settings())
    assert limits.login.limit == 3
    assert limits.chat.limit == 4
    assert [limits.login.hit("a@b.co") for _ in range(4)][-1] is not None


def test_rate_limits_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _settings(login_rate_limit_per_minute=0)
