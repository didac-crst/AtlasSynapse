"""Structured semantic review request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from semantic_memory.models.enums import SemanticClarificationStatus, SemanticReviewStage
from semantic_memory.schemas.common import MutationEnvelope

# AtlasSynapse-issued reason when a clarification request is opened.
CLARIFICATION_REASON_CODE = "semantic_distinction_unclear"


class SemanticDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    MANUAL_REVIEW = "manual_review"
    REUSE_EXISTING = "reuse_existing"
    UPHOLD_REJECTION = "uphold_rejection"


class ReviewReason(BaseModel):
    code: str
    message: str


class RelatedExistingConcept(BaseModel):
    kind: str
    key: str
    reason: str


class StructuredReviewResult(BaseModel):
    """Strict structured output from the semantic reviewer (post-policy)."""

    decision: SemanticDecision
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    reasons: list[ReviewReason] = Field(default_factory=list)
    related_existing_concepts: list[RelatedExistingConcept] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    required_clarification: list[str] = Field(default_factory=list)
    context_sufficient: bool = True
    challengeable: bool = True
    previous_decision: SemanticDecision | None = None
    decision_changed: bool | None = None


class SemanticReviewResponse(BaseModel):
    id: uuid.UUID
    proposal_id: uuid.UUID
    review_stage: SemanticReviewStage
    previous_review_id: uuid.UUID | None = None
    challenge_id: uuid.UUID | None = None
    clarification_request_id: uuid.UUID | None = None
    provider: str
    model: str
    model_version: str | None = None
    prompt_template_version: str
    context_builder_version: str
    input_hash: str
    context_concept_keys: list[str] = Field(default_factory=list)
    candidate_selection_trace: list[dict[str, Any]] = Field(default_factory=list)
    decision: SemanticDecision
    confidence: float | None = None
    summary: str
    reasons: list[ReviewReason] = Field(default_factory=list)
    related_existing_concepts: list[RelatedExistingConcept] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    required_clarification: list[str] = Field(default_factory=list)
    context_sufficient: bool = True
    challengeable: bool = True
    authoritative: bool = True
    previous_decision: SemanticDecision | None = None
    decision_changed: bool | None = None
    llm_call_log_id: uuid.UUID | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ClarificationRequestResponse(BaseModel):
    """Deterministic clarification ask issued by AtlasSynapse (not the LLM)."""

    clarification_request_id: uuid.UUID
    proposal_id: uuid.UUID
    review_id: uuid.UUID
    reason_code: str
    question: str
    required_clarification: list[str] = Field(default_factory=list)
    related_existing_concepts: list[RelatedExistingConcept] = Field(default_factory=list)
    clarification_status: SemanticClarificationStatus
    supersedes_clarification_request_id: uuid.UUID | None = None
    resolved_by_review_id: uuid.UUID | None = None
    created_at: datetime


class ChallengeOntologyReviewRequest(MutationEnvelope):
    """Challenge a reject/reuse/manual_review decision with new rationale."""

    proposal_id: uuid.UUID
    challenge_reason: str = Field(min_length=1)
    evidence_refs: list[Any] = Field(default_factory=list)
    # Reserved for future revision-aware challenges; accepted and stored in v1.
    proposed_revision: dict[str, Any] | None = None


class ChallengeOntologyReviewResponse(BaseModel):
    outcome: str
    proposal_id: uuid.UUID
    challenge_id: uuid.UUID
    review: SemanticReviewResponse
    open_clarification_request: ClarificationRequestResponse | None = None
    request_id: uuid.UUID


class AnswerSemanticClarificationRequest(MutationEnvelope):
    """Answer an AtlasSynapse-issued clarification request by ID."""

    clarification_request_id: uuid.UUID
    response: str = Field(
        min_length=1,
        description=("Clarification answer text. Field name is `response` (not `answer`)."),
    )
    evidence_refs: list[Any] = Field(default_factory=list)
    proposed_revision: dict[str, Any] | None = None


class AnswerSemanticClarificationResponse(BaseModel):
    outcome: str
    proposal_id: uuid.UUID
    clarification_request_id: uuid.UUID
    clarification_status: SemanticClarificationStatus
    resolved_by_review_id: uuid.UUID | None = None
    review: SemanticReviewResponse
    open_clarification_request: ClarificationRequestResponse | None = None
    request_id: uuid.UUID
