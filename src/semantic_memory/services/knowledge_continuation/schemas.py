"""Typed contracts for Phase E clarification / continuation."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IdentityAnswerResolution(StrEnum):
    CHOSEN_ENTITY = "chosen_entity"
    CREATE_NEW = "create_new"
    REJECT = "reject"


class ExternalBlockerKind(StrEnum):
    ONTOLOGY_APPLY = "ontology_apply"
    DEPENDENCY_STALL = "dependency_stall"
    POLICY = "policy"
    OTHER = "other"


class ClarificationAnswerInput(BaseModel):
    clarification_id: uuid.UUID
    answer_payload: dict[str, Any]


class IdentityOverride(BaseModel):
    candidate_id: uuid.UUID
    field: Literal["subject", "object"]
    resolution: IdentityAnswerResolution
    chosen_entity_id: uuid.UUID | None = None
    clarification_id: uuid.UUID
    offered_entity_ids: list[uuid.UUID] = Field(default_factory=list)


class ResolutionOverrides(BaseModel):
    """Answered package clarifications the resolver may consume (revalidated)."""

    identity_by_candidate_field: dict[str, IdentityOverride] = Field(default_factory=dict)

    def identity_for(self, candidate_id: uuid.UUID, field: str) -> IdentityOverride | None:
        return self.identity_by_candidate_field.get(f"{candidate_id}:{field}")


class OpenClarificationView(BaseModel):
    clarification_id: uuid.UUID
    clarification_key: str
    clarification_kind: str
    impact_blocked_count: int
    root_candidate_id: uuid.UUID | None = None
    question_payload: dict[str, Any] = Field(default_factory=dict)
    ontology_clarification_request_id: uuid.UUID | None = None
    supersedes_clarification_id: uuid.UUID | None = None


class ExternalBlockerView(BaseModel):
    kind: ExternalBlockerKind
    detail: str
    candidate_id: uuid.UUID | None = None
    ontology_proposal_id: uuid.UUID | None = None
    ref: str | None = None


class ContinuationResult(BaseModel):
    ingestion_id: uuid.UUID
    status: str
    ready_for_commit: bool = False
    would_complete: bool = False
    open_clarifications: list[OpenClarificationView] = Field(default_factory=list)
    unresolved_external_blockers: list[ExternalBlockerView] = Field(default_factory=list)
    eligible_count: int = 0
    blocked_count: int = 0
    discarded_count: int = 0
    paused: bool = False
    pause_reason: str | None = None
    resolver_stats: dict[str, Any] = Field(default_factory=dict)
    cumulative_stats: dict[str, Any] = Field(default_factory=dict)
