"""Operations-domain ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import ActorStatus, ActorType, BatchStatus, OperationStatus


class Actor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Operational actor distinct from knowledge entities."""

    __tablename__ = "actor"
    __table_args__ = (
        CheckConstraint(
            f"actor_type IN ({', '.join(repr(v.value) for v in ActorType)})",
            name="actor_type",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in ActorStatus)})",
            name="status",
        ),
        Index("ix_actor_status", "status"),
        Index("ix_actor_actor_type", "actor_type"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ActorStatus.ACTIVE.value
    )
    capabilities: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class IngestionBatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Batch ingestion tracking record."""

    __tablename__ = "ingestion_batch"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in BatchStatus)})",
            name="status",
        ),
        Index("ix_ingestion_batch_status", "status"),
        Index("ix_ingestion_batch_actor_id", "actor_id"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BatchStatus.PENDING.value
    )
    item_count: Mapped[int] = mapped_column(nullable=False, default=0)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )


class OperationLog(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Mutable-operation audit record."""

    __tablename__ = "operation_log"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in OperationStatus)})",
            name="status",
        ),
        Index("ix_operation_log_request_id", "request_id"),
        Index("ix_operation_log_trace_id", "trace_id"),
        Index("ix_operation_log_status", "status"),
        Index("ix_operation_log_error_code", "error_code"),
        Index("ix_operation_log_actor_id", "actor_id"),
        Index("ix_operation_log_operation_name", "operation_name"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )
    operation_name: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OperationStatus.STARTED.value
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Idempotency reservation and response cache."""

    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_idempotency_record_actor_key"),
        Index("ix_idempotency_record_request_hash", "request_hash"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    operation_name: Mapped[str] = mapped_column(Text, nullable=False)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    operation_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operation_log.id"),
    )
