"""Statement request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from semantic_memory.models.enums import StatementStatus
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.schemas.dry_run import OperationMode, StatementWriteAction
from semantic_memory.schemas.entities import EntityInput
from semantic_memory.schemas.identity import IdentityResolutionResult
from semantic_memory.schemas.memory_quality import QualityWarning

_OBJECT_FIELD_NAMES = (
    "object_entity_id",
    "object",
    "object_string",
    "object_number",
    "object_boolean",
    "object_datetime",
    "object_json",
)

EFFECTIVE_STATE_MAX_VALUES = 20
CONTEXTUAL_RELATED_FACTS_MAX = 5


class StatementReturnMode(StrEnum):
    """Projection depth for statement mutation responses (agent UX)."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    CONTEXTUAL = "contextual"


class StatementMutationRequest(MutationEnvelope):
    """Statement-write requests only — do not put return_mode on generic MutationEnvelope."""

    return_mode: StatementReturnMode = Field(
        default=StatementReturnMode.STANDARD,
        description=(
            "minimal: outcome + ids; standard: state transition for ChatGPT; "
            "contextual: standard + fixed one-hop related facts (max 5)."
        ),
    )


class AssertionOutcome(StrEnum):
    CREATE = "CREATE"
    REUSE = "REUSE"
    CLARIFY = "CLARIFY"
    SUPERSEDE = "SUPERSEDE"
    RETRACT = "RETRACT"


class EffectiveStateValue(BaseModel):
    """One effective value for subject+predicate (entity-valued or literal)."""

    statement_id: uuid.UUID
    value_kind: str
    entity_id: uuid.UUID | None = None
    canonical_name: str | None = None
    object_string: str | None = None
    object_number: Decimal | None = None
    object_boolean: bool | None = None
    object_datetime: datetime | None = None
    object_json: dict[str, Any] | list[Any] | None = None
    asserted_at: datetime


class EffectiveState(BaseModel):
    """Subject + predicate scoped effective values after a mutation."""

    subject_entity_id: uuid.UUID
    predicate_key: str
    namespace_key: str
    values: list[EffectiveStateValue] = Field(default_factory=list)
    truncated: bool = False


class MutationChanges(BaseModel):
    statement_action: StatementWriteAction | None = None
    created_statement_id: uuid.UUID | None = None
    reused_statement_id: uuid.UUID | None = None
    superseded_statement_ids: list[uuid.UUID] = Field(default_factory=list)
    retracted_statement_ids: list[uuid.UUID] = Field(default_factory=list)
    conflicts_created: list[uuid.UUID] = Field(default_factory=list)


class ClarificationCandidate(BaseModel):
    entity_id: uuid.UUID
    canonical_name: str
    types: list[str] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)
    side: str | None = None


class ClarificationAction(BaseModel):
    """Actionable CLARIFY payload — enough for ChatGPT to ask the user."""

    clarification_request_id: uuid.UUID | None = None
    question: str
    reason: str
    candidates: list[ClarificationCandidate] = Field(default_factory=list)
    resume_with: str = "answer_identity_clarification"


class AssertStatementRequest(StatementMutationRequest):
    subject_entity_id: uuid.UUID | None = None
    subject: EntityInput | None = None
    predicate_key: str = Field(min_length=1)
    namespace_key: str = Field(default="core")
    object_entity_id: uuid.UUID | None = None
    object: EntityInput | None = None
    object_string: str | None = None
    object_number: Decimal | None = None
    object_boolean: bool | None = None
    object_datetime: datetime | None = None
    object_json: dict[str, Any] | list[Any] | None = None
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    confidence: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("object_string")
    @classmethod
    def _strip_object_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _subject_and_object(self) -> AssertStatementRequest:
        if self.subject_entity_id is None and self.subject is None:
            raise ValueError("Provide subject_entity_id or subject")
        if self.subject_entity_id is not None and self.subject is not None:
            raise ValueError("Provide only one of subject_entity_id or subject")

        populated = [
            name
            for name, value in (
                ("object_entity_id", self.object_entity_id),
                ("object", self.object),
                ("object_string", self.object_string),
                ("object_number", self.object_number),
                ("object_boolean", self.object_boolean),
                ("object_datetime", self.object_datetime),
                ("object_json", self.object_json),
            )
            if value is not None
        ]
        if len(populated) != 1:
            raise ValueError("Exactly one typed object field must be provided")
        return self


class StatementResponse(BaseModel):
    id: uuid.UUID
    subject_entity_id: uuid.UUID
    predicate_id: uuid.UUID
    predicate_key: str
    namespace_key: str
    object_entity_id: uuid.UUID | None = None
    object_string: str | None = None
    object_number: Decimal | None = None
    object_boolean: bool | None = None
    object_datetime: datetime | None = None
    object_json: dict[str, Any] | list[Any] | None = None
    status: StatementStatus
    asserted_at: datetime
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    confidence: Decimal | None = None
    actor_id: uuid.UUID
    normalized_object: str | None = None
    superseded_by_statement_id: uuid.UUID | None = None
    retracts_statement_id: uuid.UUID | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelatedFact(BaseModel):
    """Bounded one-hop related effective fact for return_mode=contextual."""

    statement: StatementResponse
    ranking_score: float
    direction: str
    neighbor_entity_id: uuid.UUID | None = None


class MutationContextSlice(BaseModel):
    related_facts: list[RelatedFact] = Field(default_factory=list)
    related_facts_limit: int = CONTEXTUAL_RELATED_FACTS_MAX


class AssertStatementResponse(BaseModel):
    outcome: AssertionOutcome
    statement: StatementResponse | None = None
    request_id: uuid.UUID
    reused: bool = False
    conflict_ids: list[uuid.UUID] = Field(default_factory=list)
    subject_identity: IdentityResolutionResult | None = None
    object_identity: IdentityResolutionResult | None = None
    dry_run: bool = False
    operation_mode: OperationMode = OperationMode.EXECUTE
    would_persist: bool | None = None
    statement_action: StatementWriteAction | None = None
    clarification_request_id: uuid.UUID | None = None
    changes: MutationChanges | None = None
    effective_state: EffectiveState | None = None
    clarification: ClarificationAction | None = None
    context: MutationContextSlice | None = None
    warnings: list[str] = Field(default_factory=list)
    quality_warnings: list[QualityWarning] = Field(default_factory=list)


class SupersedeStatementRequest(StatementMutationRequest):
    """Replace an asserted statement. Omitted subject/predicate/object/temporal
    fields default to the previous statement's values (immutable history).
    """

    model_config = ConfigDict(extra="forbid")

    previous_statement_id: uuid.UUID
    subject_entity_id: uuid.UUID | None = None
    subject: EntityInput | None = None
    predicate_key: str | None = Field(default=None, min_length=1)
    namespace_key: str | None = None
    object_entity_id: uuid.UUID | None = None
    object: EntityInput | None = None
    object_string: str | None = None
    object_number: Decimal | None = None
    object_boolean: bool | None = None
    object_datetime: datetime | None = None
    object_json: dict[str, Any] | list[Any] | None = None
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    confidence: Decimal | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("object_string")
    @classmethod
    def _strip_object_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _subject_xor_and_object_when_set(self) -> SupersedeStatementRequest:
        if self.subject_entity_id is not None and self.subject is not None:
            raise ValueError("Provide only one of subject_entity_id or subject")
        object_set = [name for name in _OBJECT_FIELD_NAMES if name in self.model_fields_set]
        if object_set:
            populated = [name for name in object_set if getattr(self, name) is not None]
            if len(populated) != 1:
                raise ValueError(
                    "When overriding the object, provide exactly one typed object field"
                )
        return self


class SupersedeStatementResponse(BaseModel):
    outcome: AssertionOutcome = AssertionOutcome.SUPERSEDE
    previous_statement: StatementResponse
    statement: StatementResponse
    request_id: uuid.UUID
    reused: bool = False
    conflict_ids: list[uuid.UUID] = Field(default_factory=list)
    subject_identity: IdentityResolutionResult | None = None
    object_identity: IdentityResolutionResult | None = None
    dry_run: bool = False
    operation_mode: OperationMode = OperationMode.EXECUTE
    would_persist: bool | None = None
    statement_action: StatementWriteAction | None = None
    changes: MutationChanges | None = None
    effective_state: EffectiveState | None = None
    clarification: ClarificationAction | None = None
    context: MutationContextSlice | None = None
    warnings: list[str] = Field(default_factory=list)
    quality_warnings: list[QualityWarning] = Field(default_factory=list)


class CorrectStatementRequest(StatementMutationRequest):
    """Ergonomic temporal/object correction via supersession (not in-place UPDATE).

    Copies subject/predicate/object from ``statement_id`` unless an object_* or
    temporal field is explicitly set (including null to clear a bound).
    """

    model_config = ConfigDict(extra="forbid")

    statement_id: uuid.UUID
    object_entity_id: uuid.UUID | None = None
    object: EntityInput | None = None
    object_string: str | None = None
    object_number: Decimal | None = None
    object_boolean: bool | None = None
    object_datetime: datetime | None = None
    object_json: dict[str, Any] | list[Any] | None = None
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    confidence: Decimal | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("object_string")
    @classmethod
    def _strip_object_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _object_when_set(self) -> CorrectStatementRequest:
        object_set = [name for name in _OBJECT_FIELD_NAMES if name in self.model_fields_set]
        if object_set:
            populated = [name for name in object_set if getattr(self, name) is not None]
            if len(populated) != 1:
                raise ValueError(
                    "When overriding the object, provide exactly one typed object field"
                )
        return self


class RetractStatementRequest(StatementMutationRequest):
    model_config = ConfigDict(extra="forbid")

    statement_id: uuid.UUID
    reason: str | None = None


class RetractStatementResponse(BaseModel):
    outcome: AssertionOutcome = AssertionOutcome.RETRACT
    statement: StatementResponse
    request_id: uuid.UUID
    reason: str | None = None
    dry_run: bool = False
    operation_mode: OperationMode = OperationMode.EXECUTE
    would_persist: bool | None = None
    statement_action: StatementWriteAction | None = None
    changes: MutationChanges | None = None
    effective_state: EffectiveState | None = None
    context: MutationContextSlice | None = None
    warnings: list[str] = Field(default_factory=list)
    quality_warnings: list[QualityWarning] = Field(default_factory=list)


class TimelineEntry(BaseModel):
    statement: StatementResponse
    sort_time: datetime


class TimelineResponse(BaseModel):
    entity_id: uuid.UUID
    entries: list[TimelineEntry] = Field(default_factory=list)
