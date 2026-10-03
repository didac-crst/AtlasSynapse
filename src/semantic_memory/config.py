"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        default="postgresql+psycopg://semantic_memory:semantic_memory@localhost:5432/semantic_memory",
        alias="DATABASE_URL",
    )
    database_pool_size: int = Field(default=5, alias="DATABASE_POOL_SIZE", ge=1)
    database_max_overflow: int = Field(default=10, alias="DATABASE_MAX_OVERFLOW", ge=0)
    raw_payload_retention: Literal["none", "redacted", "full"] = Field(
        default="redacted",
        alias="RAW_PAYLOAD_RETENTION",
    )
    semantic_review_mode: Literal["disabled", "mock", "external"] = Field(
        default="disabled",
        alias="SEMANTIC_REVIEW_MODE",
    )
    mcp_transport: Literal["stdio", "http"] = Field(default="stdio", alias="MCP_TRANSPORT")
    http_host: str = Field(default="0.0.0.0", alias="HTTP_HOST")
    http_port: int = Field(default=8000, alias="HTTP_PORT", ge=1, le=65535)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL SQLAlchemy URL")
        return value

    @property
    def sqlalchemy_database_uri(self) -> str:
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
