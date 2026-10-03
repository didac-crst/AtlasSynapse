"""Entity request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from semantic_memory.models.enums import EntityStatus
from semantic_memory.schemas.common import MutationEnvelope


class ResolutionOutcome(StrEnum):
    CREATE = "CREATE"
    REUSE = "REUSE"
    AMBIGUOUS = "AMBIGUOUS"


class ExternalReferenceInput(BaseModel):
    source_system: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    uri: str | None = None
    label: str | None = None


class CreateEntityRequest(MutationEnvelope):
    canonical_name: str = Field(min_length=1)
    class_key: str = Field(min_length=1, description="Ontology class key in the core namespace")
    namespace_key: str = Field(default="core")
    aliases: list[str] = Field(default_factory=list)
    external_reference: ExternalReferenceInput | None = None


class EntityTypeResponse(BaseModel):
    class_id: uuid.UUID
    class_key: str
    namespace_key: str


class ExternalReferenceResponse(BaseModel):
    source_system: str
    external_id: str
    uri: str | None = None
    label: str | None = None


class EntityCandidate(BaseModel):
    id: uuid.UUID
    canonical_name: str
    status: EntityStatus
    class_keys: list[str] = Field(default_factory=list)
    match_reason: str


class EntityResponse(BaseModel):
    id: uuid.UUID
    canonical_name: str
    status: EntityStatus
    merged_into_entity_id: uuid.UUID | None = None
    types: list[EntityTypeResponse] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    external_references: list[ExternalReferenceResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateEntityResponse(BaseModel):
    outcome: ResolutionOutcome
    entity: EntityResponse | None = None
    candidates: list[EntityCandidate] = Field(default_factory=list)
    request_id: uuid.UUID
    reused: bool = False
