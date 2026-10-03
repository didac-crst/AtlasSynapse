"""LLM call observability rows, separate from operation_log.

Each call is one retained row created at start and completed in place.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import LlmCallStatus, LlmCostStatus


class LlmCallLog(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Provider-neutral durable record of one LLM/provider call (begin/complete)."""

    __tablename__ = "llm_call_log"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in LlmCallStatus)})",
            name="status",
        ),
        CheckConstraint(
            f"cost_status IN ({', '.join(repr(v.value) for v in LlmCostStatus)})",
            name="cost_status",
        ),
        Index("ix_llm_call_log_actor_id", "actor_id"),
        Index("ix_llm_call_log_operation_log_id", "operation_log_id"),
        Index("ix_llm_call_log_request_id", "request_id"),
        Index("ix_llm_call_log_trace_id", "trace_id"),
        Index("ix_llm_call_log_provider", "provider"),
        Index("ix_llm_call_log_purpose", "purpose"),
        Index("ix_llm_call_log_status", "status"),
        Index("ix_llm_call_log_started_at", "started_at"),
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
    )
    operation_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operation_log.id"),
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    cost_currency: Mapped[str | None] = mapped_column(String(16))
    cost_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LlmCostStatus.UNKNOWN.value
    )
    pricing_version: Mapped[str | None] = mapped_column(Text)
    pricing_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
