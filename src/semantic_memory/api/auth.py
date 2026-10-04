"""HTTP authentication boundary for non-health API routes."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from semantic_memory.config import Settings, get_settings
from semantic_memory.schemas.errors import ErrorEnvelope


def _extract_api_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth is not None:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
    header_token = request.headers.get("x-api-token")
    if header_token:
        return header_token.strip()
    return None


def api_auth_required(settings: Settings) -> bool:
    """Return whether HTTP API token auth is enforced."""
    if settings.app_env == "production":
        return True
    return bool(settings.http_api_token)


class HttpApiTokenMiddleware(BaseHTTPMiddleware):
    """Require a shared API token for application routes.

    Health probes stay public. In non-production, auth is enforced only when
    ``HTTP_API_TOKEN`` is configured (fail-open for local tests). Production
    always requires a non-empty token.
    """

    def __init__(self, app: object, settings: Settings | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path.startswith("/health"):
            return await call_next(request)
        if path in {"/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        settings = self._settings or get_settings()
        if not api_auth_required(settings):
            return await call_next(request)

        expected = settings.http_api_token
        provided = _extract_api_token(request)
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            body = ErrorEnvelope(
                error_code="UNAUTHORIZED",
                message="Valid API token required",
                details={"headers": ["Authorization: Bearer <token>", "X-API-Token"]},
                retryable=False,
            )
            return JSONResponse(status_code=401, content=body.model_dump(mode="json"))
        return await call_next(request)
