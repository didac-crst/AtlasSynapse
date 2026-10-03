"""Batch ingestion request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from semantic_memory.models.enums import BatchStatus
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.schemas.entities import (
    CreateEntityResponse,
    EntityCandidate,
    ExternalReferenceInput,
)
from semantic_memory.schemas.statements import AssertStatementResponse, StatementResponse


class BatchEntityItem(BaseModel):
    client_item_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    class_key: str = Field(min_length=1)
    namespace_key: str = Field(default="core")
    aliases: list[str] = Field(default_factory=list)
    external_reference: ExternalReferenceInput | None = None


class BatchStatementItem(BaseModel):
    client_item_id: str = Field(min_length=1)
    subject_entity_id: uuid.UUID | None = None
    subject_client_item_id: str | None = None
    predicate_key: str = Field(min_length=1)
    namespace_key: str = Field(default="core")
    object_entity_id: uuid.UUID | None = None
    object_client_item_id: str | None = None
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
    def _subject_and_object(self) -> BatchStatementItem:
        if self.subject_entity_id is None and self.subject_client_item_id is None:
            raise ValueError("Provide subject_entity_id or subject_client_item_id")
        if self.subject_entity_id is not None and self.subject_client_item_id is not None:
            raise ValueError("Provide only one subject reference")
        if self.object_entity_id is not None and self.object_client_item_id is not None:
            raise ValueError("Provide only one object entity reference")
        populated = [
            name
            for name, value in (
                ("object_entity_id", self.object_entity_id),
                ("object_client_item_id", self.object_client_item_id),
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


class AssertBatchRequest(MutationEnvelope):
    entities: list[BatchEntityItem] = Field(default_factory=list)
    statements: list[BatchStatementItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _non_empty(self) -> AssertBatchRequest:
        if not self.entities and not self.statements:
            raise ValueError("Batch must include at least one entity or statement item")
        return self


class BatchItemOutcome(StrEnum):
    CREATED = "created"
    REUSED = "reused"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"
    ONTOLOGY_REQUIRED = "ontology_required"


class BatchItemResult(BaseModel):
    client_item_id: str
    outcome: BatchItemOutcome
    entity: CreateEntityResponse | None = None
    statement: AssertStatementResponse | None = None
    candidates: list[EntityCandidate] = Field(default_factory=list)
    statement_ref: StatementResponse | None = None
    error_code: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AssertBatchResponse(BaseModel):
    batch_id: uuid.UUID
    status: BatchStatus
    created: list[BatchItemResult] = Field(default_factory=list)
    reused: list[BatchItemResult] = Field(default_factory=list)
    ambiguous: list[BatchItemResult] = Field(default_factory=list)
    rejected: list[BatchItemResult] = Field(default_factory=list)
    ontology_required: list[BatchItemResult] = Field(default_factory=list)
    request_id: uuid.UUID
