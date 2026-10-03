"""Health endpoint response schemas."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness response."""

    status: str


class ReadyResponse(BaseModel):
    """Readiness response."""

    status: str
    database: str
    migrations: str
