"""Stable transport error schemas."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    """Public error envelope shared by HTTP and MCP boundaries."""

    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    retryable: bool = False
