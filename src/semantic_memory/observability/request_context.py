"""Per-request context for optional debug timings."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

debug_timings_enabled: ContextVar[bool] = ContextVar("debug_timings_enabled", default=False)
last_read_timings: ContextVar[dict[str, Any] | None] = ContextVar("last_read_timings", default=None)


def set_last_timings(payload: dict[str, Any]) -> None:
    # Middleware seeds a mutable holder before call_next. Sync FastAPI routes run
    # in a threadpool that copies ContextVars, so rebinding via .set() would not
    # be visible to the middleware — mutate the shared holder instead.
    holder = last_read_timings.get()
    if holder is not None:
        holder.clear()
        holder.update(payload)
        return
    last_read_timings.set(dict(payload))
