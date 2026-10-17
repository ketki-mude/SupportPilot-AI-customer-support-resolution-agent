"""Application settings, loaded once from environment variables."""

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LLMProvider(StrEnum):
    """Chat model vendors the agent can run on."""

    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Settings(BaseSettings):
    """Every configurable value in the backend. Nothing else reads the environment."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: str
    test_database_url: str | None = None
    db_command_timeout_seconds: float = Field(default=15, gt=0, le=300)

    # Hardening for every request.
    request_timeout_seconds: float = Field(default=30, gt=0, le=600)
    max_request_bytes: int = Field(default=64 * 1024, ge=1024)
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    chat_rate_limit_per_minute: int = Field(default=10, ge=1)
    login_rate_limit_per_minute: int = Field(default=10, ge=1)

    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=24 * 60)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
    # A just-rotated refresh token still works this long, so two tabs refreshing at once
    # do not look like token theft.
    refresh_reuse_grace_seconds: int = Field(default=10, ge=0, le=300)
    # Defaults to secure cookies in production only, so local http works.
    cookie_secure: bool | None = None
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    llm_provider: LLMProvider = LLMProvider.GEMINI
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = Field(default=30, gt=0, le=300)
    # USD per million tokens for the configured model; output includes any thinking tokens.
    llm_input_usd_per_million: Decimal = Field(default=Decimal("0.30"), ge=0)
    llm_output_usd_per_million: Decimal = Field(default=Decimal("2.50"), ge=0)
    google_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None
    rag_top_k: int = Field(default=4, ge=1, le=20)
    rag_min_similarity: float = Field(default=0.65, ge=0, le=1)

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "supportpilot"

    mock_commerce_url: str = "http://localhost:8001"
    # Evaluations: their own database and their own shop, never the demo ones.
    eval_database_url: str | None = None
    eval_commerce_url: str = "http://localhost:8002"
    # Where the dataset and saved reports live. Unset: the evals/ folder next to the backend.
    eval_dir: str | None = None
    # Recorded in every evaluation report; `make` passes the current commit.
    eval_git_commit: str = "unknown"
    commerce_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    commerce_max_attempts: int = Field(default=3, ge=1, le=10)
    commerce_backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    # After this many failed calls in a row, stop calling for the cooldown and escalate fast.
    commerce_breaker_threshold: int = Field(default=5, ge=1)
    commerce_breaker_cooldown_seconds: float = Field(default=30, gt=0)
    agent_run_timeout_seconds: float = Field(default=120, gt=0, le=1800)

    refund_window_days: int = Field(default=30, ge=1)
    auto_approve_max_usd: Decimal = Field(default=Decimal("50"), ge=0)
    supervisor_approval_max_usd: Decimal = Field(default=Decimal("500"), gt=0)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    cancel_use_refund_limits: bool = False
    max_clarification_turns: int = Field(default=5, ge=1, le=20)

    seed_customer_password: SecretStr | None = None
    seed_agent_password: SecretStr | None = None
    seed_supervisor_password: SecretStr | None = None
    seed_admin_password: SecretStr | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """CORS_ORIGINS is a comma-separated list, e.g. "https://a.example,https://b.example"."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def secure_cookies(self) -> bool:
        return (
            self.cookie_secure if self.cookie_secure is not None else self.app_env == "production"
        )

    @model_validator(mode="after")
    def _check_refund_thresholds(self) -> Self:
        """The auto-approve ceiling must sit below the supervisor ceiling."""
        if self.auto_approve_max_usd >= self.supervisor_approval_max_usd:
            raise ValueError("AUTO_APPROVE_MAX_USD must be lower than SUPERVISOR_APPROVAL_MAX_USD")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
