"""Phase D: dependency-aware resolution agenda (commit planning only)."""

from semantic_memory.services.knowledge_resolution.schemas import (
    CandidateResolution,
    ResolutionBlocker,
    ResolveIngestionStats,
)
from semantic_memory.services.knowledge_resolution.service import KnowledgeResolutionService

__all__ = [
    "CandidateResolution",
    "KnowledgeResolutionService",
    "ResolutionBlocker",
    "ResolveIngestionStats",
]
