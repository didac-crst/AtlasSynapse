"""Conflict and merge request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from semantic_memory.models.enums import ConflictStatus
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.schemas.entities import EntityResponse


class ConflictResponse(BaseModel):
    id: uuid.UUID
    statement_a_id: uuid.UUID
    statement_b_id: uuid.UUID
    status: ConflictStatus
    conflict_type: str
    details: dict[str, Any] = Field(default_factory=dict)
    resolved_by_actor_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class FindConflictsResponse(BaseModel):
    conflicts: list[ConflictResponse] = Field(default_factory=list)


class ResolveConflictRequest(MutationEnvelope):
    conflict_id: uuid.UUID
    resolution_note: str | None = None


class DismissConflictRequest(MutationEnvelope):
    conflict_id: uuid.UUID
    resolution_note: str | None = None


class ConflictMutationResponse(BaseModel):
    conflict: ConflictResponse
    request_id: uuid.UUID


class MergeEntityRequest(MutationEnvelope):
    source_entity_id: uuid.UUID
    target_entity_id: uuid.UUID


class MergeEntityResponse(BaseModel):
    source: EntityResponse
    target: EntityResponse
    request_id: uuid.UUID
