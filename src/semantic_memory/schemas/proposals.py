"""Ontology proposal request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from semantic_memory.models.enums import (
    AliasTargetType,
    Cardinality,
    ConstraintType,
    GateDecision,
    ProposalStatus,
    ProposalType,
    ValueKind,
)
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.schemas.dry_run import OperationMode
from semantic_memory.schemas.semantic_review import (
    ClarificationRequestResponse,
    SemanticReviewResponse,
)


class ProposeClassRequest(MutationEnvelope):
    namespace_key: str = Field(
        default="core",
        description="Ontology namespace. Use 'core' for production; 'smoke' for disposable tests.",
    )
    key: str = Field(min_length=1)
    label: str | None = None
    description: str | None = None
    parent_keys: list[str] = Field(default_factory=list)
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProposePredicateRequest(MutationEnvelope):
    """Propose a predicate.

    Write fields use ``domain_keys`` / ``range_keys``. Read APIs return
    ``domain_class_keys`` / ``range_class_keys``; those aliases are also accepted
    here so clients are not forced to rename.
    """

    model_config = ConfigDict(populate_by_name=True)

    namespace_key: str = Field(
        default="core",
        description="Ontology namespace. Use 'core' for production; 'smoke' for disposable tests.",
    )
    key: str = Field(min_length=1)
    label: str | None = None
    description: str | None = None
    value_kind: ValueKind
    datatype: str | None = None
    cardinality: Cardinality = Cardinality.MANY
    domain_keys: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("domain_keys", "domain_class_keys"),
        serialization_alias="domain_keys",
        description="Domain class keys (not domain_class_keys from get_predicate).",
    )
    range_keys: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("range_keys", "range_class_keys"),
        serialization_alias="range_keys",
        description="Range class keys (not range_class_keys from get_predicate).",
    )
    is_symmetric: bool = False
    is_transitive: bool = False
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    base_revision_number: int | None = None


class ProposeConstraintRequest(MutationEnvelope):
    namespace_key: str = Field(default="core")
    key: str = Field(min_length=1)
    constraint_type: ConstraintType
    expression: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    summary: str | None = None


class ProposeAliasRequest(MutationEnvelope):
    namespace_key: str = Field(default="core")
    alias: str = Field(min_length=1)
    target_type: AliasTargetType
    target_key: str = Field(min_length=1)
    summary: str | None = None


class ProposeClassParentRequest(MutationEnvelope):
    namespace_key: str = Field(default="core")
    child_key: str = Field(min_length=1)
    parent_key: str = Field(min_length=1)
    summary: str | None = None
    base_revision_number: int | None = None


class ApplyProposalRequest(MutationEnvelope):
    proposal_id: uuid.UUID


class GateResultResponse(BaseModel):
    gate_name: str
    decision: GateDecision
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OntologyChangeResponse(BaseModel):
    id: uuid.UUID
    object_type: str
    object_id: uuid.UUID
    previous_revision_id: uuid.UUID | None = None
    new_revision_id: uuid.UUID | None = None
    change_summary: str
    applied_by_actor_id: uuid.UUID
    created_at: datetime


class ProposalOutcome(StrEnum):
    READY_TO_APPLY = "READY_TO_APPLY"
    REJECTED = "REJECTED"
    REUSE_RECOMMENDED = "REUSE_RECOMMENDED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    APPLIED = "APPLIED"
    WOULD_APPLY = "WOULD_APPLY"


class ProposalResponse(BaseModel):
    id: uuid.UUID
    proposal_type: ProposalType
    status: ProposalStatus
    summary: str
    payload: dict[str, Any]
    base_revision_number: int | None = None
    request_id: uuid.UUID
    decision_reason: str | None = None
    gate_results: list[GateResultResponse] = Field(default_factory=list)
    changes: list[OntologyChangeResponse] = Field(default_factory=list)
    effective_semantic_review_id: uuid.UUID | None = None
    effective_semantic_review: SemanticReviewResponse | None = None
    semantic_reviews: list[SemanticReviewResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ProposeResponse(BaseModel):
    outcome: ProposalOutcome
    proposal: ProposalResponse
    open_clarification_request: ClarificationRequestResponse | None = None
    request_id: uuid.UUID


class ApplyProposalResponse(BaseModel):
    outcome: ProposalOutcome
    proposal: ProposalResponse
    request_id: uuid.UUID
    dry_run: bool = False
    operation_mode: OperationMode = OperationMode.EXECUTE
    would_persist: bool | None = None
    projected: bool = False
    """When true, ``proposal`` / ``changes`` describe a dry-run projection, not committed state."""
