"""Provenance-domain ORM models."""

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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Source(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Reusable provenance source."""

    __tablename__ = "source"
    __table_args__ = (
        CheckConstraint(
            "reliability IS NULL OR (reliability >= 0 AND reliability <= 1)",
            name="reliability_bounds",
        ),
        Index("ix_source_external_id", "external_id"),
        Index("ix_source_content_hash", "content_hash"),
        Index("ix_source_source_system", "source_system"),
    )

    source_system: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    uri: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    reliability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )


class StatementEvidence(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Evidence linking a statement to a source."""

    __tablename__ = "statement_evidence"
    __table_args__ = (
        CheckConstraint(
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="extraction_confidence_bounds",
        ),
        Index("ix_statement_evidence_statement_id", "statement_id"),
        Index("ix_statement_evidence_source_id", "source_id"),
    )

    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("statement.id"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source.id"),
        nullable=False,
    )
    excerpt: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(Text)
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    asserted_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )


class ExternalReference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """External identity reference for an entity."""

    __tablename__ = "external_reference"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_id",
            name="uq_external_reference_system_external_id",
        ),
        Index("ix_external_reference_entity_id", "entity_id"),
        Index("ix_external_reference_source_system", "source_system"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
        nullable=False,
    )
    source_system: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(512))
