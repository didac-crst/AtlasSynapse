"""Phase E: clarification planner and continue_ingestion (no live knowledge commit)."""

from semantic_memory.services.knowledge_continuation.schemas import (
    ClarificationAnswerInput,
    ContinuationResult,
    IdentityAnswerResolution,
)
from semantic_memory.services.knowledge_continuation.service import KnowledgeContinuationService

__all__ = [
    "ClarificationAnswerInput",
    "ContinuationResult",
    "IdentityAnswerResolution",
    "KnowledgeContinuationService",
]
