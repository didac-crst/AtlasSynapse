"""Knowledge-domain ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import EntityStatus, StatementStatus

_OBJECT_EXCLUSIVITY = (
    "("
    "(CASE WHEN object_entity_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_string IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_number IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_boolean IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_datetime IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_json IS NOT NULL THEN 1 ELSE 0 END)"
    ") = 1"
)

_QUALIFIER_OBJECT_EXCLUSIVITY = (
    "("
    "(CASE WHEN object_entity_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_string IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_number IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_boolean IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_datetime IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN object_json IS NOT NULL THEN 1 ELSE 0 END)"
    ") = 1"
)


class Entity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Knowledge entity."""

    __tablename__ = "entity"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in EntityStatus)})",
            name="status",
        ),
        Index("ix_entity_canonical_name", "canonical_name"),
        Index("ix_entity_status", "status"),
        Index("ix_entity_merged_into_entity_id", "merged_into_entity_id"),
    )

    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EntityStatus.ACTIVE.value
    )
    merged_into_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
    )
    created_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )


class EntityType(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Entity-to-ontology-class typing."""

    __tablename__ = "entity_type"
    __table_args__ = (
        UniqueConstraint("entity_id", "class_id", name="uq_entity_type_entity_class"),
        Index("ix_entity_type_entity_id", "entity_id"),
        Index("ix_entity_type_class_id", "class_id"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
        nullable=False,
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_class.id"),
        nullable=False,
    )
    asserted_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )


class EntityAlias(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Alternate name for an entity."""

    __tablename__ = "entity_alias"
    __table_args__ = (
        UniqueConstraint("entity_id", "alias", name="uq_entity_alias_entity_alias"),
        Index("ix_entity_alias_alias", "alias"),
        Index("ix_entity_alias_normalized_alias", "normalized_alias"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source.id"),
    )


class Statement(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Subject-predicate-object statement with temporal and lifecycle fields."""

    __tablename__ = "statement"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in StatementStatus)})",
            name="status",
        ),
        CheckConstraint(_OBJECT_EXCLUSIVITY, name="object_exclusivity"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_interval",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_bounds",
        ),
        Index("ix_statement_subject_entity_id", "subject_entity_id"),
        Index("ix_statement_predicate_id", "predicate_id"),
        Index("ix_statement_object_entity_id", "object_entity_id"),
        Index("ix_statement_status", "status"),
        Index("ix_statement_asserted_at", "asserted_at"),
        Index("ix_statement_valid_from", "valid_from"),
        Index("ix_statement_valid_to", "valid_to"),
        Index("ix_statement_actor_id", "actor_id"),
        Index(
            "ix_statement_semantic_identity",
            "subject_entity_id",
            "predicate_id",
            "object_entity_id",
            "object_string",
            "object_number",
            "object_boolean",
            "object_datetime",
            "valid_from",
            "valid_to",
        ),
    )

    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
        nullable=False,
    )
    predicate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_predicate.id"),
        nullable=False,
    )
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
    )
    object_string: Mapped[str | None] = mapped_column(Text)
    object_number: Mapped[Decimal | None] = mapped_column(Numeric)
    object_boolean: Mapped[bool | None] = mapped_column(Boolean)
    object_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    object_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=StatementStatus.ASSERTED.value,
    )
    asserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )
    superseded_by_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("statement.id"),
    )
    retracts_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("statement.id"),
    )
    normalized_object: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )


class StatementQualifier(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Qualifier attached to a statement with exactly one typed object."""

    __tablename__ = "statement_qualifier"
    __table_args__ = (
        CheckConstraint(_QUALIFIER_OBJECT_EXCLUSIVITY, name="object_exclusivity"),
        Index("ix_statement_qualifier_statement_id", "statement_id"),
        Index("ix_statement_qualifier_predicate_id", "predicate_id"),
    )

    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("statement.id"),
        nullable=False,
    )
    predicate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_predicate.id"),
        nullable=False,
    )
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity.id"),
    )
    object_string: Mapped[str | None] = mapped_column(Text)
    object_number: Mapped[Decimal | None] = mapped_column(Numeric)
    object_boolean: Mapped[bool | None] = mapped_column(Boolean)
    object_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    object_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
