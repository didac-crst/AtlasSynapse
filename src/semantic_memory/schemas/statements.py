"""Statement request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from semantic_memory.models.enums import StatementStatus
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.schemas.entities import EntityInput
from semantic_memory.schemas.identity import IdentityResolutionResult


class AssertionOutcome(StrEnum):
    CREATE = "CREATE"
    REUSE = "REUSE"
    CLARIFY = "CLARIFY"


class AssertStatementRequest(MutationEnvelope):
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


class AssertStatementResponse(BaseModel):
    outcome: AssertionOutcome
    statement: StatementResponse | None = None
    request_id: uuid.UUID
    reused: bool = False
    conflict_ids: list[uuid.UUID] = Field(default_factory=list)
    subject_identity: IdentityResolutionResult | None = None
    object_identity: IdentityResolutionResult | None = None


class SupersedeStatementRequest(AssertStatementRequest):
    previous_statement_id: uuid.UUID


class SupersedeStatementResponse(BaseModel):
    previous_statement: StatementResponse
    statement: StatementResponse
    request_id: uuid.UUID


class RetractStatementRequest(MutationEnvelope):
    statement_id: uuid.UUID


class RetractStatementResponse(BaseModel):
    statement: StatementResponse
    request_id: uuid.UUID


class TimelineEntry(BaseModel):
    statement: StatementResponse
    sort_time: datetime


class TimelineResponse(BaseModel):
    entity_id: uuid.UUID
    entries: list[TimelineEntry] = Field(default_factory=list)
