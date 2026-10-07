"""Optional HTTP timing headers for latency diagnosis."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from semantic_memory.observability.request_context import (
    debug_timings_enabled,
    last_read_timings,
)


class ReadTimingMiddleware(BaseHTTPMiddleware):
    """Attach cheap total timing; detailed stages when ``X-Debug-Timings: 1``."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        want_detail = request.headers.get("x-debug-timings", "").strip() in {
            "1",
            "true",
            "yes",
        }
        token = debug_timings_enabled.set(want_detail)
        timings_token = last_read_timings.set(None)
        started = time.perf_counter()
        detail = None
        try:
            response = await call_next(request)
            detail = last_read_timings.get()
        finally:
            debug_timings_enabled.reset(token)
            last_read_timings.reset(timings_token)
        total_ms = round((time.perf_counter() - started) * 1000, 3)
        response.headers["X-Atlas-Server-Ms"] = str(total_ms)
        if want_detail and detail is not None:
            response.headers["X-Atlas-Timings"] = json.dumps(detail, separators=(",", ":"))
        return response
