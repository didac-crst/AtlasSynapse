"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Header

from semantic_memory.config import get_settings
from semantic_memory.exceptions import UnauthorizedOperationError


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    """Require the configured admin token for internal provisioning routes."""
    settings = get_settings()
    expected = settings.admin_api_token
    if not expected or x_admin_token != expected:
        raise UnauthorizedOperationError(
            "Admin token required for actor provisioning",
            details={"header": "X-Admin-Token"},
        )
