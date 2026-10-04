"""Agent feedback observations, separate from operation_log and proposals."""

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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import FeedbackSeverity, FeedbackStatus, FeedbackType


class AgentFeedback(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Actionable observation reported by an agent about system quality."""

    __tablename__ = "agent_feedback"
    __table_args__ = (
        CheckConstraint(
            f"feedback_type IN ({', '.join(repr(v.value) for v in FeedbackType)})",
            name="feedback_type",
        ),
        CheckConstraint(
            f"severity IN ({', '.join(repr(v.value) for v in FeedbackSeverity)})",
            name="severity",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in FeedbackStatus)})",
            name="status",
        ),
        Index("ix_agent_feedback_actor_id", "actor_id"),
        Index("ix_agent_feedback_feedback_type", "feedback_type"),
        Index("ix_agent_feedback_severity", "severity"),
        Index("ix_agent_feedback_status", "status"),
        Index("ix_agent_feedback_fingerprint", "fingerprint"),
        Index(
            "uq_agent_feedback_open_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status = 'open' AND fingerprint IS NOT NULL"),
        ),
        Index("ix_agent_feedback_request_id", "request_id"),
        Index("ix_agent_feedback_created_at", "created_at"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )
    feedback_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    operation_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operation_log.id"),
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_proposal.id"),
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
    )
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("statement.id"),
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source.id"),
    )
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    fingerprint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=FeedbackStatus.OPEN.value,
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
