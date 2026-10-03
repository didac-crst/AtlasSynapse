"""Reasoning-domain ORM models."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import ConflictStatus


class Conflict(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Authoritative conflict metadata between statements."""

    __tablename__ = "conflict"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in ConflictStatus)})",
            name="status",
        ),
        Index("ix_conflict_statement_a_id", "statement_a_id"),
        Index("ix_conflict_statement_b_id", "statement_b_id"),
        Index("ix_conflict_status", "status"),
    )

    statement_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("statement.id"),
        nullable=False,
    )
    statement_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("statement.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConflictStatus.OPEN.value
    )
    conflict_type: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    resolved_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
    )
