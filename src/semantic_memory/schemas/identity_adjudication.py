"""Bounded LLM adjudication payloads for entity identity (PR5)."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from semantic_memory.schemas.identity import CandidateDecision, IdentityCandidate


class IdentityLlmDecision(StrEnum):
    """Strict model output enum (mapped to CandidateDecision at the boundary)."""

    SAME_ENTITY = "SAME_ENTITY"
    DIFFERENT_ENTITY = "DIFFERENT_ENTITY"
    UNCERTAIN = "UNCERTAIN"


class PackagedEvidenceItem(BaseModel):
    """One evidence row exposed to the model (stable opaque id)."""

    evidence_id: str = Field(min_length=1)
    signal: str
    strength: str
    value: str | None = None
    namespace: str | None = None
    predicate: str | None = None
    detail: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class PackagedCandidate(BaseModel):
    candidate_id: str = Field(min_length=1)
    entity_id: uuid.UUID
    canonical_name: str
    class_keys: list[str] = Field(default_factory=list)
    evidence: list[PackagedEvidenceItem] = Field(default_factory=list)


class BoundedIdentityPackage(BaseModel):
    """Prompt-ready evidence package: no DB handles, hard-capped."""

    incoming_canonical_name: str
    namespace_key: str = "core"
    class_key: str | None = None
    candidates: list[PackagedCandidate] = Field(default_factory=list)
    truncated_candidates: int = 0
    truncated_evidence: int = 0


class IdentityAdjudicationRequest(BaseModel):
    """Service input before packaging (UNCERTAIN candidates only)."""

    incoming_canonical_name: str
    namespace_key: str = "core"
    class_key: str | None = None
    candidates: list[IdentityCandidate] = Field(default_factory=list)


class IdentityCandidateAdjudication(BaseModel):
    entity_id: uuid.UUID
    candidate_id: str | None = None
    decision: CandidateDecision
    llm_decision: IdentityLlmDecision | None = None
    summary: str | None = None
    cited_evidence_ids: list[str] = Field(default_factory=list)
    rejected_invented_evidence_ids: list[str] = Field(default_factory=list)


class IdentityAdjudicationResult(BaseModel):
    candidate_decisions: list[IdentityCandidateAdjudication] = Field(default_factory=list)
    provider: str = "disabled"
    model: str = "none"
    prompt_template_version: str | None = None
    enforced: bool = False
    shadow: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("candidate_decisions")
    @classmethod
    def _ok(cls, value: list[IdentityCandidateAdjudication]) -> list[IdentityCandidateAdjudication]:
        return value


def map_llm_decision(decision: IdentityLlmDecision) -> CandidateDecision:
    if decision == IdentityLlmDecision.SAME_ENTITY:
        return CandidateDecision.SAME
    if decision == IdentityLlmDecision.DIFFERENT_ENTITY:
        return CandidateDecision.DIFFERENT
    return CandidateDecision.UNCERTAIN
