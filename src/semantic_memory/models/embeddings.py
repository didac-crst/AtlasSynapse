"""Optional derived embedding storage. Never authoritative for truth or identity."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import EmbeddingObjectType


class Embedding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Derived vector index row. Safe to delete and rebuild."""

    __tablename__ = "embedding"
    __table_args__ = (
        CheckConstraint(
            f"object_type IN ({', '.join(repr(v.value) for v in EmbeddingObjectType)})",
            name="object_type",
        ),
        UniqueConstraint(
            "object_type",
            "object_id",
            "model_key",
            name="uq_embedding_object_model",
        ),
        Index("ix_embedding_object_type", "object_type"),
        Index("ix_embedding_object_id", "object_id"),
        Index("ix_embedding_model_key", "model_key"),
    )

    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_key: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
    )
