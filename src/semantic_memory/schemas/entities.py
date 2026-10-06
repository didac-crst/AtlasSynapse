"""Entity request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from semantic_memory.models.enums import AliasIdentityStrength, EntityStatus
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.schemas.identity import IdentityResolutionResult


class ResolutionOutcome(StrEnum):
    """Legacy create_entity outcome (action-shaped). Prefer IdentityResolutionOutcome."""

    CREATE = "CREATE"
    REUSE = "REUSE"
    AMBIGUOUS = "AMBIGUOUS"


class ExternalReferenceInput(BaseModel):
    source_system: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    uri: str | None = None
    label: str | None = None


class EntityInput(BaseModel):
    """Typed unresolved (or already-resolved) entity reference for write paths.

    Prefer this over a bare name string: callers should supply class context and
    any known aliases / external refs so identity resolve is well-posed.
    """

    entity_id: uuid.UUID | None = None
    canonical_name: str | None = None
    class_key: str | None = None
    namespace_key: str = Field(default="core")
    aliases: list[str] = Field(default_factory=list)
    external_refs: list[ExternalReferenceInput] = Field(default_factory=list)

    @field_validator("canonical_name", "class_key")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _require_identity_context_when_unresolved(self) -> EntityInput:
        if self.entity_id is not None:
            return self
        if self.canonical_name is None:
            raise ValueError("canonical_name is required when entity_id is omitted")
        if self.class_key is None:
            raise ValueError("class_key is required when entity_id is omitted")
        return self


class CreateEntityRequest(MutationEnvelope):
    canonical_name: str = Field(min_length=1)
    class_key: str = Field(min_length=1, description="Ontology class key in the core namespace")
    namespace_key: str = Field(default="core")
    aliases: list[str] = Field(default_factory=list)
    external_reference: ExternalReferenceInput | None = None


class AddEntityAliasRequest(MutationEnvelope):
    entity_id: uuid.UUID
    alias: str = Field(min_length=1)
    identity_strength: AliasIdentityStrength = AliasIdentityStrength.SUPPORTING


class EntityTypeResponse(BaseModel):
    class_id: uuid.UUID
    class_key: str
    namespace_key: str


class ExternalReferenceResponse(BaseModel):
    source_system: str
    external_id: str
    uri: str | None = None
    label: str | None = None


class EntityAliasEntry(BaseModel):
    alias: str
    identity_strength: AliasIdentityStrength


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
    alias_entries: list[EntityAliasEntry] = Field(default_factory=list)
    external_references: list[ExternalReferenceResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateEntityResponse(BaseModel):
    outcome: ResolutionOutcome
    entity: EntityResponse | None = None
    candidates: list[EntityCandidate] = Field(default_factory=list)
    request_id: uuid.UUID
    reused: bool = False
    identity: IdentityResolutionResult | None = None
