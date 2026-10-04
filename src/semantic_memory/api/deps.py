"""Shared FastAPI dependencies."""

from __future__ import annotations

import hmac

from fastapi import Header

from semantic_memory.config import get_settings
from semantic_memory.exceptions import UnauthorizedOperationError


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    """Require the configured admin token for administrative routes."""
    settings = get_settings()
    expected = settings.admin_api_token
    if not expected or not hmac.compare_digest(x_admin_token or "", expected):
        raise UnauthorizedOperationError(
            "Admin token required for this administrative route",
            details={"header": "X-Admin-Token"},
        )
