"""Durable working-state models for governed knowledge ingestion (Phase B).

Audit/replay substrate only — no extraction or commit orchestration here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import (
    KnowledgeCandidateDependencyKind,
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    KnowledgeCandidateState,
    KnowledgeIngestionClarificationKind,
    KnowledgeIngestionClarificationStatus,
    KnowledgeIngestionEffectStatus,
    KnowledgeIngestionEffectType,
    KnowledgeIngestionMode,
    KnowledgeIngestionPauseReason,
    KnowledgeIngestionStatus,
)


class KnowledgeIngestion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One package-ingestion attempt (execute or dry_run)."""

    __tablename__ = "knowledge_ingestion"
    __table_args__ = (
        CheckConstraint(
            "mode IN (" + ", ".join(repr(v.value) for v in KnowledgeIngestionMode) + ")",
            name="mode",
        ),
        CheckConstraint(
            "status IN (" + ", ".join(repr(v.value) for v in KnowledgeIngestionStatus) + ")",
            name="status",
        ),
        CheckConstraint(
            "pause_reason IS NULL OR pause_reason IN ("
            + ", ".join(repr(v.value) for v in KnowledgeIngestionPauseReason)
            + ")",
            name="pause_reason",
        ),
        # Dry-run runs must never enter committing.
        CheckConstraint(
            "NOT (mode = 'dry_run' AND status = 'committing')",
            name="dry_run_never_committing",
        ),
        UniqueConstraint(
            "actor_id",
            "idempotency_key",
            name="uq_knowledge_ingestion_actor_idempotency_key",
        ),
        Index("ix_knowledge_ingestion_status", "status"),
        Index("ix_knowledge_ingestion_actor_id", "actor_id"),
        Index("ix_knowledge_ingestion_source_id", "source_id"),
        Index("ix_knowledge_ingestion_request_id", "request_id"),
        Index("ix_knowledge_ingestion_mode", "mode"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actor.id"), nullable=False
    )
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=KnowledgeIngestionMode.EXECUTE.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=KnowledgeIngestionStatus.ACCEPTED.value
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id"), nullable=False
    )
    source_content_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_content_revision.id")
    )
    document_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity.id")
    )
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(Text, nullable=False)
    ontology_revision_marker: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_model: Mapped[str | None] = mapped_column(Text)
    extraction_prompt_version: Mapped[str | None] = mapped_column(Text)
    budgets_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    stats_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    pause_reason: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Staged semantic candidate within one ingestion package."""

    __tablename__ = "knowledge_candidate"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(repr(v.value) for v in KnowledgeCandidateKind) + ")",
            name="kind",
        ),
        CheckConstraint(
            "polarity IN (" + ", ".join(repr(v.value) for v in KnowledgeCandidatePolarity) + ")",
            name="polarity",
        ),
        CheckConstraint(
            "epistemic_status IN ("
            + ", ".join(repr(v.value) for v in KnowledgeCandidateEpistemicStatus)
            + ")",
            name="epistemic_status",
        ),
        CheckConstraint(
            "derivation IN ("
            + ", ".join(repr(v.value) for v in KnowledgeCandidateDerivation)
            + ")",
            name="derivation",
        ),
        CheckConstraint(
            "state IN (" + ", ".join(repr(v.value) for v in KnowledgeCandidateState) + ")",
            name="state",
        ),
        UniqueConstraint(
            "ingestion_id",
            "candidate_key",
            name="uq_knowledge_candidate_ingestion_candidate_key",
        ),
        Index("ix_knowledge_candidate_ingestion_id", "ingestion_id"),
        Index("ix_knowledge_candidate_state", "state"),
        Index("ix_knowledge_candidate_kind", "kind"),
        Index("ix_knowledge_candidate_epistemic_status", "epistemic_status"),
    )

    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_ingestion.id"), nullable=False
    )
    candidate_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    polarity: Mapped[str] = mapped_column(String(16), nullable=False)
    epistemic_status: Mapped[str] = mapped_column(String(32), nullable=False)
    derivation: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_text: Mapped[str | None] = mapped_column(Text)
    claim_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_span: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_context_path: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=KnowledgeCandidateState.EXTRACTED.value
    )
    blockers_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolution_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    committed_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity.id")
    )
    committed_statement_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class KnowledgeCandidateDependency(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Directed prerequisite edge: child waits on parent.

    Wake-up semantics: when ``parent_candidate_id`` advances enough to clear the
    dependency, ``child_candidate_id`` may become actionable again.
    """

    __tablename__ = "knowledge_candidate_dependency"
    __table_args__ = (
        CheckConstraint(
            "dependency_kind IN ("
            + ", ".join(repr(v.value) for v in KnowledgeCandidateDependencyKind)
            + ")",
            name="dependency_kind",
        ),
        CheckConstraint(
            "parent_candidate_id <> child_candidate_id",
            name="no_self_dependency",
        ),
        UniqueConstraint(
            "parent_candidate_id",
            "child_candidate_id",
            "dependency_kind",
            name="uq_knowledge_candidate_dependency_edge",
        ),
        Index("ix_knowledge_candidate_dependency_parent", "parent_candidate_id"),
        Index("ix_knowledge_candidate_dependency_child", "child_candidate_id"),
    )

    parent_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_candidate.id"), nullable=False
    )
    child_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_candidate.id"), nullable=False
    )
    dependency_kind: Mapped[str] = mapped_column(String(32), nullable=False)


class KnowledgeIngestionClarification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Package-level clarification planner record (not a parallel clarification framework)."""

    __tablename__ = "knowledge_ingestion_clarification"
    __table_args__ = (
        CheckConstraint(
            "clarification_kind IN ("
            + ", ".join(repr(v.value) for v in KnowledgeIngestionClarificationKind)
            + ")",
            name="clarification_kind",
        ),
        CheckConstraint(
            "status IN ("
            + ", ".join(repr(v.value) for v in KnowledgeIngestionClarificationStatus)
            + ")",
            name="status",
        ),
        Index("ix_knowledge_ingestion_clarification_ingestion_id", "ingestion_id"),
        Index("ix_knowledge_ingestion_clarification_status", "status"),
        Index(
            "ix_ki_clarification_write_clarification_request_id",
            "write_clarification_request_id",
        ),
    )

    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_ingestion.id"), nullable=False
    )
    root_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_candidate.id")
    )
    clarification_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    question_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    answer_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=KnowledgeIngestionClarificationStatus.OPEN.value,
    )
    impact_blocked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    write_clarification_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("write_clarification_request.id")
    )
    ontology_clarification_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ontology_semantic_clarification_request.id")
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeIngestionEffect(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Resumable effect log entry keyed by stable effect_key within a candidate."""

    __tablename__ = "knowledge_ingestion_effect"
    __table_args__ = (
        CheckConstraint(
            "effect_type IN ("
            + ", ".join(repr(v.value) for v in KnowledgeIngestionEffectType)
            + ")",
            name="effect_type",
        ),
        CheckConstraint(
            "status IN (" + ", ".join(repr(v.value) for v in KnowledgeIngestionEffectStatus) + ")",
            name="status",
        ),
        UniqueConstraint(
            "ingestion_id",
            "candidate_id",
            "effect_key",
            name="uq_knowledge_ingestion_effect_key",
        ),
        Index("ix_knowledge_ingestion_effect_ingestion_id", "ingestion_id"),
        Index("ix_knowledge_ingestion_effect_candidate_id", "candidate_id"),
        Index("ix_knowledge_ingestion_effect_status", "status"),
        Index("ix_knowledge_ingestion_effect_idempotency_key", "idempotency_key"),
    )

    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_ingestion.id"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_candidate.id"), nullable=False
    )
    effect_key: Mapped[str] = mapped_column(Text, nullable=False)
    effect_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=KnowledgeIngestionEffectStatus.FAILED.value
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operation_log.id")
    )
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statement.id")
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("entity.id"))
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
