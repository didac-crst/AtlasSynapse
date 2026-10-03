"""Provenance request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from semantic_memory.schemas.common import MutationEnvelope


class SourceOutcome(StrEnum):
    CREATE = "CREATE"
    REUSE = "REUSE"


class SourceInput(BaseModel):
    source_id: uuid.UUID | None = None
    source_system: str | None = None
    external_id: str | None = None
    uri: str | None = None
    title: str | None = None
    content_hash: str | None = None
    reliability: Decimal | None = None
    retrieved_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _has_identity(self) -> SourceInput:
        if self.source_id is not None:
            return self
        if self.source_system and self.external_id:
            return self
        if self.content_hash or self.uri or self.title:
            return self
        raise ValueError(
            "Source requires source_id, external identity, content_hash, uri, or title"
        )


class SourceResponse(BaseModel):
    id: uuid.UUID
    source_system: str | None = None
    external_id: str | None = None
    uri: str | None = None
    title: str | None = None
    content_hash: str | None = None
    reliability: Decimal | None = None
    retrieved_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class EnsureSourceRequest(MutationEnvelope, SourceInput):
    pass


class EnsureSourceResponse(BaseModel):
    outcome: SourceOutcome
    source: SourceResponse
    request_id: uuid.UUID
    reused: bool = False


class EvidenceResponse(BaseModel):
    id: uuid.UUID
    statement_id: uuid.UUID
    source: SourceResponse
    excerpt: str | None = None
    locator: str | None = None
    extraction_confidence: Decimal | None = None
    asserted_by_actor_id: uuid.UUID
    created_at: datetime


class AddEvidenceRequest(MutationEnvelope):
    statement_id: uuid.UUID
    source: SourceInput
    excerpt: str | None = None
    locator: str | None = None
    extraction_confidence: Decimal | None = None


class AddEvidenceResponse(BaseModel):
    outcome: SourceOutcome
    evidence: EvidenceResponse
    request_id: uuid.UUID
    reused: bool = False


class ExplainStatementResponse(BaseModel):
    statement_id: uuid.UUID
    asserted_at: datetime
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: str
    evidence: list[EvidenceResponse] = Field(default_factory=list)
