"""Application settings, loaded once from environment variables."""

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1, le=24 * 60)
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
    commerce_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    commerce_max_attempts: int = Field(default=3, ge=1, le=10)
    commerce_backoff_seconds: float = Field(default=0.5, ge=0, le=10)

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
