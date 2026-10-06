"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES


class Settings(BaseSettings):
    """Runtime settings for AtlasSynapse."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field(default="AtlasSynapse", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(
        default=(
            "postgresql+psycopg://semantic_memory:semantic_memory@localhost:5432/semantic_memory"
        ),
        alias="DATABASE_URL",
    )
    database_pool_size: int = Field(default=5, alias="DATABASE_POOL_SIZE", ge=1)
    database_max_overflow: int = Field(default=10, alias="DATABASE_MAX_OVERFLOW", ge=0)
    raw_payload_retention: Literal["none", "redacted", "full"] = Field(
        default="redacted",
        alias="RAW_PAYLOAD_RETENTION",
    )
    semantic_review_mode: Literal["disabled", "mock", "shadow", "external"] = Field(
        default="disabled",
        alias="SEMANTIC_REVIEW_MODE",
    )
    semantic_review_provider: str = Field(default="openai", alias="SEMANTIC_REVIEW_PROVIDER")
    semantic_review_model: str = Field(default="gpt-5.6-terra", alias="SEMANTIC_REVIEW_MODEL")
    semantic_review_model_version: str | None = Field(
        default=None, alias="SEMANTIC_REVIEW_MODEL_VERSION"
    )
    semantic_review_api_key: str = Field(default="", alias="SEMANTIC_REVIEW_API_KEY")
    semantic_review_api_base: str = Field(
        default="https://api.openai.com/v1",
        alias="SEMANTIC_REVIEW_API_BASE",
    )
    semantic_review_reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="low",
        alias="SEMANTIC_REVIEW_REASONING_EFFORT",
    )
    semantic_review_max_candidates: int = Field(
        default=8, alias="SEMANTIC_REVIEW_MAX_CANDIDATES", ge=1, le=50
    )
    semantic_review_max_context_tokens: int = Field(
        default=2000, alias="SEMANTIC_REVIEW_MAX_CONTEXT_TOKENS", ge=200, le=32000
    )
    semantic_review_max_output_tokens: int = Field(
        default=500, alias="SEMANTIC_REVIEW_MAX_OUTPUT_TOKENS", ge=64, le=4000
    )
    semantic_review_approve_threshold: float = Field(
        default=0.85, alias="SEMANTIC_REVIEW_APPROVE_THRESHOLD", ge=0.0, le=1.0
    )
    semantic_review_reject_threshold: float = Field(
        default=0.85, alias="SEMANTIC_REVIEW_REJECT_THRESHOLD", ge=0.0, le=1.0
    )
    semantic_review_input_rate_per_token: str = Field(
        default="0.000002",
        alias="SEMANTIC_REVIEW_INPUT_RATE_PER_TOKEN",
    )
    semantic_review_output_rate_per_token: str = Field(
        default="0.000012",
        alias="SEMANTIC_REVIEW_OUTPUT_RATE_PER_TOKEN",
    )
    semantic_review_currency: str = Field(default="USD", alias="SEMANTIC_REVIEW_CURRENCY")
    semantic_review_timeout_seconds: float = Field(
        default=45.0, alias="SEMANTIC_REVIEW_TIMEOUT_SECONDS", gt=0
    )
    identity_review_mode: Literal["disabled", "mock", "shadow", "external"] = Field(
        default="disabled",
        alias="IDENTITY_REVIEW_MODE",
    )
    identity_review_provider: str = Field(default="openai", alias="IDENTITY_REVIEW_PROVIDER")
    identity_review_model: str = Field(
        default="gpt-5.6-terra",
        alias="IDENTITY_REVIEW_MODEL",
    )
    identity_review_api_key: str = Field(default="", alias="IDENTITY_REVIEW_API_KEY")
    identity_review_api_base: str = Field(
        default="",
        alias="IDENTITY_REVIEW_API_BASE",
    )
    identity_review_reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="low",
        alias="IDENTITY_REVIEW_REASONING_EFFORT",
    )
    identity_review_max_candidates: int = Field(
        default=4, alias="IDENTITY_REVIEW_MAX_CANDIDATES", ge=1, le=8
    )
    identity_review_max_evidence_per_candidate: int = Field(
        default=8, alias="IDENTITY_REVIEW_MAX_EVIDENCE_PER_CANDIDATE", ge=1, le=20
    )
    identity_review_max_output_tokens: int = Field(
        default=400, alias="IDENTITY_REVIEW_MAX_OUTPUT_TOKENS", ge=64, le=2000
    )
    identity_review_timeout_seconds: float = Field(
        default=30.0, alias="IDENTITY_REVIEW_TIMEOUT_SECONDS", gt=0
    )
    embedding_mode: Literal["disabled", "mock"] = Field(
        default="disabled",
        alias="EMBEDDING_MODE",
    )
    mcp_transport: Literal["stdio", "http"] = Field(default="stdio", alias="MCP_TRANSPORT")
    http_host: str = Field(default="0.0.0.0", alias="HTTP_HOST")
    http_port: int = Field(default=8000, alias="HTTP_PORT", ge=1, le=65535)
    http_api_token: str = Field(default="", alias="HTTP_API_TOKEN")
    admin_api_token: str = Field(default="", alias="ADMIN_API_TOKEN")
    default_actor_capabilities: list[str] = Field(
        default_factory=lambda: [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        alias="DEFAULT_ACTOR_CAPABILITIES",
    )

    def validate_production_secrets(self) -> None:
        """Fail closed when production is missing required shared secrets."""
        if self.app_env != "production":
            return
        missing: list[str] = []
        if not self.http_api_token:
            missing.append("HTTP_API_TOKEN")
        if not self.admin_api_token:
            missing.append("ADMIN_API_TOKEN")
        if "semantic_memory:semantic_memory@" in self.database_url:
            missing.append("DATABASE_URL (development default credentials)")
        if missing:
            raise ValueError("Production configuration is incomplete: " + ", ".join(missing))

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL SQLAlchemy URL")
        return value

    @field_validator("default_actor_capabilities")
    @classmethod
    def validate_default_capabilities(cls, value: list[str]) -> list[str]:
        if "admin" in value:
            raise ValueError("DEFAULT_ACTOR_CAPABILITIES must not include admin")
        return value

    @property
    def sqlalchemy_database_uri(self) -> str:
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
