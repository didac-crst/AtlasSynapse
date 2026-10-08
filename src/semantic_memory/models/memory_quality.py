"""Memory quality residual issue ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import (
    MemoryQualityIssueStatus,
    MemoryQualityIssueType,
    MemoryQualityResolution,
    MemoryQualitySeverity,
)


class MemoryQualityIssue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """System-detected post-write residual quality problem."""

    __tablename__ = "memory_quality_issue"
    __table_args__ = (
        CheckConstraint(
            "issue_type IN ("
            + ", ".join(repr(v.value) for v in MemoryQualityIssueType)
            + ")",
            name="issue_type",
        ),
        CheckConstraint(
            "status IN (" + ", ".join(repr(v.value) for v in MemoryQualityIssueStatus) + ")",
            name="status",
        ),
        CheckConstraint(
            "severity IN (" + ", ".join(repr(v.value) for v in MemoryQualitySeverity) + ")",
            name="severity",
        ),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ("
            + ", ".join(repr(v.value) for v in MemoryQualityResolution)
            + ")",
            name="resolution",
        ),
        Index("ix_memory_quality_issue_status", "status"),
        Index("ix_memory_quality_issue_issue_type", "issue_type"),
        Index("ix_memory_quality_issue_subject_entity_id", "subject_entity_id"),
        Index("ix_memory_quality_issue_statement_id", "statement_id"),
        Index("ix_memory_quality_issue_updated_at", "updated_at"),
        Index("ix_memory_quality_issue_fingerprint", "fingerprint"),
        Index(
            "uq_memory_quality_issue_open_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MemoryQualityIssueStatus.OPEN.value
    )
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    detector_key: Mapped[str] = mapped_column(Text, nullable=False)
    detector_version: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity.id")
    )
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity.id")
    )
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statement.id")
    )
    related_entity_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    related_statement_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    requires_clarification: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    trigger_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operation_log.id")
    )
    last_seen_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operation_log.id")
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolution: Mapped[str | None] = mapped_column(String(64))
    resolution_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operation_log.id")
    )
    resolved_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actor.id")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
