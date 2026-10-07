"""Lightweight monotonic request timing for latency diagnosis."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("semantic_memory.timing")


@dataclass
class RequestTimer:
    """Accumulate stage durations with ``time.perf_counter()``."""

    operation: str
    request_id: str | None = None
    _t0: float = field(default_factory=time.perf_counter)
    _marks: dict[str, float] = field(default_factory=dict)
    stages_ms: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self._marks[name] = time.perf_counter()

    def stage(self, name: str, *, since: str | None = None) -> None:
        """Record elapsed ms since start (or since a prior mark)."""
        now = time.perf_counter()
        base = self._marks[since] if since is not None else self._t0
        self.stages_ms[name] = round((now - base) * 1000, 3)
        self._marks[name] = now

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages_ms[name] = round((time.perf_counter() - start) * 1000, 3)

    def finish(self) -> dict[str, Any]:
        total = round((time.perf_counter() - self._t0) * 1000, 3)
        self.stages_ms["total"] = total
        payload = {
            "operation": self.operation,
            "request_id": self.request_id,
            "timings_ms": dict(self.stages_ms),
        }
        logger.info(
            "read_timing",
            extra={
                "event": "read_timing",
                "operation": self.operation,
                "request_id": self.request_id,
                "timings_ms": dict(self.stages_ms),
            },
        )
        return payload
