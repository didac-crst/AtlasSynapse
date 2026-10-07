"""Per-request context for optional debug timings."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

debug_timings_enabled: ContextVar[bool] = ContextVar("debug_timings_enabled", default=False)
last_read_timings: ContextVar[dict[str, Any] | None] = ContextVar("last_read_timings", default=None)


def set_last_timings(payload: dict[str, Any]) -> None:
    # Always store; middleware decides whether to expose via header.
    # Note: sync FastAPI routes may run in a threadpool where ContextVar
    # propagation from async middleware is not guaranteed — prefer response
    # body metadata / structured logs for authoritative stage timings.
    last_read_timings.set(payload)
