"""Phase C knowledge extraction: structured YAML/JSON → staged candidates."""

from semantic_memory.services.knowledge_extraction.candidate_keys import (
    build_candidate_key,
    semantic_fingerprint,
)
from semantic_memory.services.knowledge_extraction.classifier import EXTRACTOR_VERSION
from semantic_memory.services.knowledge_extraction.service import KnowledgeExtractionService

__all__ = [
    "EXTRACTOR_VERSION",
    "KnowledgeExtractionService",
    "build_candidate_key",
    "semantic_fingerprint",
]
