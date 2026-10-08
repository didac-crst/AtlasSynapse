"""Memory quality detector protocol."""

from __future__ import annotations

from typing import Protocol

from semantic_memory.schemas.memory_quality import ChangeContext, QualityFinding


class MemoryQualityDetector(Protocol):
    key: str
    version: str

    def inspect(self, change_context: ChangeContext) -> list[QualityFinding]:
        """Inspect the affected neighborhood only."""
        ...
