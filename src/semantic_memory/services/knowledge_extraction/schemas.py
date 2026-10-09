"""Pydantic contracts for knowledge extraction drafts and LLM output."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from semantic_memory.models.enums import (
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
)

SkipReason = Literal[
    "metadata",
    "container",
    "empty",
    "non_semantic",
    "unsupported_semantics",
    "unsupported_value",
    "external_classifier_failed",
]


class ClaimPayloadHint(BaseModel):
    """Extraction hints only — never resolved entity/predicate IDs."""

    subject: dict[str, Any] | None = None
    predicate_key_hint: str | None = None
    predicate_text: str | None = None
    object: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: str | None = None
    raw: Any | None = None

    model_config = {"extra": "allow"}


class CandidateDraft(BaseModel):
    """One staged candidate prior to persistence."""

    candidate_key: str
    kind: KnowledgeCandidateKind
    polarity: KnowledgeCandidatePolarity
    epistemic_status: KnowledgeCandidateEpistemicStatus
    derivation: KnowledgeCandidateDerivation
    claim_text: str
    claim_payload: dict[str, Any] = Field(default_factory=dict)
    source_span: dict[str, Any] = Field(default_factory=dict)
    source_context_path: list[str | int] = Field(default_factory=list)
    ordinal: int = 0
    classification_basis: list[str] = Field(default_factory=list)


class FragmentSkip(BaseModel):
    path: list[str | int]
    reason: SkipReason
    detail: str | None = None


class LlmFragmentClassification(BaseModel):
    """Strict structured LLM output for one fragment."""

    path: list[str | int]
    action: Literal["candidate", "skip"]
    kind: KnowledgeCandidateKind | None = None
    polarity: KnowledgeCandidatePolarity | None = None
    epistemic_status: KnowledgeCandidateEpistemicStatus | None = None
    derivation: KnowledgeCandidateDerivation | None = None
    claim_text: str | None = None
    claim_payload: ClaimPayloadHint | None = None
    skip_reason: SkipReason | None = None
    basis: list[str] = Field(default_factory=list)


class LlmExtractionBatchResult(BaseModel):
    classifications: list[LlmFragmentClassification]


class ExtractionStats(BaseModel):
    fragments_seen: int = 0
    fragments_skipped: int = 0
    candidates_created: int = 0
    candidates_reused: int = 0
    inferred_candidates: int = 0
    normalized_candidates: int = 0
    explicit_candidates: int = 0
    skipped_by_reason: dict[str, int] = Field(default_factory=dict)
    skipped: list[FragmentSkip] = Field(default_factory=list)
    package_format: str | None = None
    extractor_version: str | None = None
    classifier: str | None = None
