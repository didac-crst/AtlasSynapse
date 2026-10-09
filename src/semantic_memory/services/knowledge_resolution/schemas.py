"""Typed resolution / blocker contracts for knowledge ingestion Phase D."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

RESOLUTION_SCHEMA_VERSION: Literal["knowledge-resolution-v1"] = "knowledge-resolution-v1"


class CommitPath(StrEnum):
    DOMAIN_ASSERTION = "domain_assertion"
    CLAIM = "claim"


class ProjectionDisposition(StrEnum):
    REUSE = "reuse"
    CREATE = "create"
    LITERAL = "literal"
    OMIT = "omit"
    CLARIFY = "clarify"
    PROPOSAL = "proposal"
    DESCRIPTIVE_HINT = "descriptive_hint"


class BlockerType(StrEnum):
    IDENTITY_CLARIFICATION = "identity_clarification"
    ONTOLOGY_PROPOSAL = "ontology_proposal"
    DEPENDENCY = "dependency"
    POLICY = "policy"


class ResolutionWarningCode(StrEnum):
    AMBIGUOUS_OPTIONAL_PROJECTION = "ambiguous_optional_projection"
    OMITTED_OPTIONAL_PROJECTION = "omitted_optional_projection"
    CLAIM_FALLBACK_UNSAFE_DOMAIN = "claim_fallback_unsafe_domain"
    INSUFFICIENT_STRUCTURE_FOR_DOMAIN_ASSERTION = "insufficient_structure_for_domain_assertion"
    WOULD_PROPOSE_ONTOLOGY = "would_propose_ontology"
    REUSE_RECOMMENDED = "reuse_recommended"
    POSSIBLE_REUSE_DISCOVERY = "possible_reuse_discovery"
    PREDICATE_HINT_NON_ASSERTIVE = "predicate_hint_non_assertive"


class EntityBindPlan(BaseModel):
    disposition: ProjectionDisposition
    text: str | None = None
    entity_id: uuid.UUID | None = None
    class_key: str | None = None
    candidate_entity_ids: list[uuid.UUID] = Field(default_factory=list)
    detail: str | None = None


class PredicateBindPlan(BaseModel):
    disposition: ProjectionDisposition
    predicate_key_hint: str | None = None
    predicate_id: uuid.UUID | None = None
    predicate_key: str | None = None
    proposal_id: uuid.UUID | None = None
    detail: str | None = None


class ObjectBindPlan(BaseModel):
    disposition: ProjectionDisposition
    text: str | None = None
    entity_id: uuid.UUID | None = None
    literal_value: Any | None = None
    class_key: str | None = None
    candidate_entity_ids: list[uuid.UUID] = Field(default_factory=list)
    detail: str | None = None


class ResolutionWarning(BaseModel):
    code: ResolutionWarningCode
    detail: str
    field: str | None = None


class CandidateResolution(BaseModel):
    """Validated resolution plan persisted as ``resolution_json``."""

    schema_version: Literal["knowledge-resolution-v1"] = RESOLUTION_SCHEMA_VERSION
    commit_path: CommitPath
    subject: EntityBindPlan = Field(
        default_factory=lambda: EntityBindPlan(disposition=ProjectionDisposition.OMIT)
    )
    predicate: PredicateBindPlan = Field(
        default_factory=lambda: PredicateBindPlan(disposition=ProjectionDisposition.OMIT)
    )
    object: ObjectBindPlan = Field(
        default_factory=lambda: ObjectBindPlan(disposition=ProjectionDisposition.OMIT)
    )
    ontology_proposal_ids: list[uuid.UUID] = Field(default_factory=list)
    warnings: list[ResolutionWarning] = Field(default_factory=list)
    would_propose_ontology: list[dict[str, Any]] = Field(default_factory=list)
    claim_text: str | None = None
    epistemic_kind: str | None = None  # for Claim path: hypothesis|recommendation|…


class ResolutionBlocker(BaseModel):
    """Typed blocker persisted in ``blockers_json``."""

    type: BlockerType
    field: str | None = None
    ref: str | None = None
    detail: str
    required: bool = True
    write_clarification_request_id: uuid.UUID | None = None
    ontology_proposal_id: uuid.UUID | None = None
    ontology_clarification_request_id: uuid.UUID | None = None
    parent_candidate_id: uuid.UUID | None = None


class ResolveIngestionStats(BaseModel):
    candidates_seen: int = 0
    candidates_attempted: int = 0
    eligible: int = 0
    blocked: int = 0
    claim_path: int = 0
    domain_path: int = 0
    ontology_proposals_created: int = 0
    ontology_proposals_reused: int = 0
    identity_blockers: int = 0
    dependents_woken: int = 0
    llm_calls: int = 0
    budget_exhausted: bool = False
    dependency_stall: bool = False
    wall_ms: int = 0
