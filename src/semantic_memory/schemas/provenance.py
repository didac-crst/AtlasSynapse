"""Provenance request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from semantic_memory.schemas.common import MutationEnvelope

ContentFormat = Literal["markdown", "text", "html"]


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
    entity_id: uuid.UUID | None = None
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
    entity_id: uuid.UUID | None = None
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


class SourceContentRevisionResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    revision_number: int
    canonical_content: str
    canonical_format: ContentFormat
    canonical_content_hash: str
    original_content: str | None = None
    original_format: ContentFormat | None = None
    original_content_hash: str | None = None
    captured_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by_actor_id: uuid.UUID


class IngestSourceContentRequest(MutationEnvelope):
    """Ingest immutable source content with backend-owned canonicalization.

    Preferred shape:
      content + content_format (+ optional canonical_format, default markdown)

    AtlasSynapse preserves the exact original, generates canonical content
    deterministically, and stores both SHA-256 hashes plus canonicalizer
    identity. Approximate publication dates belong in metadata
    (published_at + date_precision), not fabricated datetimes.

    Legacy fields (canonical_content / original_*) remain accepted and are
    mapped onto the same path for one release.
    """

    source: SourceInput
    document_entity_id: uuid.UUID | None = None
    content: str | None = Field(default=None, min_length=1)
    content_format: ContentFormat | None = None
    canonical_format: ContentFormat = "markdown"
    # Legacy explicit bodies (optional during transition).
    canonical_content: str | None = Field(default=None, min_length=1)
    original_content: str | None = None
    original_format: ContentFormat | None = None
    captured_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _resolve_content_fields(self) -> IngestSourceContentRequest:
        if self.content is not None:
            if self.content_format is None:
                raise ValueError("content_format is required when content is set")
            return self
        if self.canonical_content is not None:
            # Legacy: treat pre-canonicalized body as already-markdown content,
            # optionally with a separate original payload.
            return self
        if self.original_content is not None and self.original_format is not None:
            # Legacy: original only — canonicalize from original.
            return self
        raise ValueError(
            "Provide content+content_format, or legacy canonical_content / original_content"
        )


class IngestSourceContentResponse(BaseModel):
    outcome: SourceOutcome
    source: SourceResponse
    revision: SourceContentRevisionResponse
    request_id: uuid.UUID
    reused: bool = False


class GetSourceContentRequest(BaseModel):
    source_id: uuid.UUID | None = None
    document_entity_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    include_original: bool = True

    @model_validator(mode="after")
    def _has_locator(self) -> GetSourceContentRequest:
        if self.revision_id is not None:
            return self
        if self.source_id is not None or self.document_entity_id is not None:
            return self
        raise ValueError("Provide revision_id, source_id, or document_entity_id")


class GetSourceContentResponse(BaseModel):
    source: SourceResponse
    revision: SourceContentRevisionResponse


class SearchSourceContentRequest(BaseModel):
    query: str = Field(min_length=1)
    source_id: uuid.UUID | None = None
    document_entity_id: uuid.UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    context_chars: int = Field(default=120, ge=0, le=2000)


class SourceContentPassage(BaseModel):
    source: SourceResponse
    revision_id: uuid.UUID
    revision_number: int
    excerpt: str
    start_offset: int
    end_offset: int
    locator: str


class SearchSourceContentResponse(BaseModel):
    query: str
    passages: list[SourceContentPassage] = Field(default_factory=list)
