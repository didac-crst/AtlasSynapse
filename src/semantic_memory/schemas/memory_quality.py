"""Memory quality schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from semantic_memory.models.enums import (
    MemoryQualityIssueStatus,
    MemoryQualityIssueType,
    MemoryQualityResolution,
    MemoryQualitySeverity,
    QualityRouteOutcome,
)


class QualityWarning(BaseModel):
    """Bounded agent-facing quality warning for mutation/retrieval envelopes."""

    issue_id: uuid.UUID
    type: MemoryQualityIssueType
    severity: MemoryQualitySeverity
    summary: str
    requires_clarification: bool = False
    related_entity_ids: list[uuid.UUID] = Field(default_factory=list)
    related_statement_ids: list[uuid.UUID] = Field(default_factory=list)


class QualityFinding(BaseModel):
    """Detector output before routing."""

    issue_type: MemoryQualityIssueType
    severity: MemoryQualitySeverity
    detector_key: str
    detector_version: str
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    subject_entity_id: uuid.UUID | None = None
    object_entity_id: uuid.UUID | None = None
    statement_id: uuid.UUID | None = None
    related_entity_ids: list[uuid.UUID] = Field(default_factory=list)
    related_statement_ids: list[uuid.UUID] = Field(default_factory=list)
    requires_clarification: bool = False
    recommended_route: QualityRouteOutcome = QualityRouteOutcome.OPEN_QUALITY_ISSUE
    # Optional conflict upsert payload (cardinality clash owned by ConflictService).
    conflict_new_statement_id: uuid.UUID | None = None
    conflict_existing_statement_ids: list[uuid.UUID] = Field(default_factory=list)
    conflict_predicate_key: str | None = None


class ChangeContext(BaseModel):
    """Neighborhood scope for post-write quality inspection."""

    actor_id: uuid.UUID | None = None
    operation_id: uuid.UUID | None = None
    operation_name: str = ""
    request_id: uuid.UUID | None = None
    touched_entity_ids: list[uuid.UUID] = Field(default_factory=list)
    touched_statement_ids: list[uuid.UUID] = Field(default_factory=list)
    touched_predicate_ids: list[uuid.UUID] = Field(default_factory=list)
    supersession_edges: list[tuple[uuid.UUID, uuid.UUID]] = Field(default_factory=list)
    skip_fingerprints: list[str] = Field(default_factory=list)
    dry_run: bool = False


class MemoryQualityIssueResponse(BaseModel):
    id: uuid.UUID
    issue_type: MemoryQualityIssueType
    status: MemoryQualityIssueStatus
    severity: MemoryQualitySeverity
    detector_key: str
    detector_version: str
    fingerprint: str
    subject_entity_id: uuid.UUID | None = None
    object_entity_id: uuid.UUID | None = None
    statement_id: uuid.UUID | None = None
    related_entity_ids: list[uuid.UUID] = Field(default_factory=list)
    related_statement_ids: list[uuid.UUID] = Field(default_factory=list)
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    requires_clarification: bool = False
    occurrence_count: int = 1
    resolution: MemoryQualityResolution | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class MemoryQualityIssueListResponse(BaseModel):
    items: list[MemoryQualityIssueResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
