"""Health endpoint response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness response."""

    status: str


class ReadyResponse(BaseModel):
    """Readiness response."""

    status: str
    database: str
    migrations: str


class ConfigHealthResponse(BaseModel):
    """Non-secret runtime configuration used for deploy parity checks."""

    status: str = "ok"
    config: dict[str, Any] = Field(default_factory=dict)
