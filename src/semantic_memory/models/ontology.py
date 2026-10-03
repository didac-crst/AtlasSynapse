"""Ontology-domain ORM models."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import AliasTargetType, Cardinality, ConstraintType, ValueKind


class OntologyNamespace(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Ontology namespace."""

    __tablename__ = "ontology_namespace"
    __table_args__ = (UniqueConstraint("key", name="uq_ontology_namespace_key"),)

    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class OntologyClass(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Ontology class with current revision pointer."""

    __tablename__ = "ontology_class"
    __table_args__ = (
        UniqueConstraint("namespace_id", "key", name="uq_ontology_class_namespace_key"),
        Index("ix_ontology_class_namespace_id", "namespace_id"),
        Index("ix_ontology_class_key", "key"),
    )

    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_namespace.id"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_class_revision.id", use_alter=True, name="fk_ontology_class_current_revision"
        ),
    )
    is_deprecated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OntologyClassRevision(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Immutable ontology class revision."""

    __tablename__ = "ontology_class_revision"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "revision_number", name="uq_ontology_class_revision_class_number"
        ),
        Index("ix_ontology_class_revision_class_id", "class_id"),
    )

    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_class.id"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
    )


class OntologyClassParent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Directed parent link between ontology classes."""

    __tablename__ = "ontology_class_parent"
    __table_args__ = (
        UniqueConstraint("child_class_id", "parent_class_id", name="uq_ontology_class_parent_pair"),
        CheckConstraint("child_class_id <> parent_class_id", name="no_self_parent"),
        Index("ix_ontology_class_parent_child", "child_class_id"),
        Index("ix_ontology_class_parent_parent", "parent_class_id"),
    )

    child_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_class.id"),
        nullable=False,
    )
    parent_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_class.id"),
        nullable=False,
    )


class OntologyPredicate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Ontology predicate with current revision pointer."""

    __tablename__ = "ontology_predicate"
    __table_args__ = (
        UniqueConstraint("namespace_id", "key", name="uq_ontology_predicate_namespace_key"),
        Index("ix_ontology_predicate_namespace_id", "namespace_id"),
        Index("ix_ontology_predicate_key", "key"),
    )

    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_namespace.id"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_predicate_revision.id",
            use_alter=True,
            name="fk_ontology_predicate_current_revision",
        ),
    )
    is_deprecated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OntologyPredicateRevision(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Immutable ontology predicate revision."""

    __tablename__ = "ontology_predicate_revision"
    __table_args__ = (
        UniqueConstraint(
            "predicate_id",
            "revision_number",
            name="uq_ontology_predicate_revision_predicate_number",
        ),
        CheckConstraint(
            f"value_kind IN ({', '.join(repr(v.value) for v in ValueKind)})",
            name="value_kind",
        ),
        CheckConstraint(
            f"cardinality IN ({', '.join(repr(v.value) for v in Cardinality)})",
            name="cardinality",
        ),
        Index("ix_ontology_predicate_revision_predicate_id", "predicate_id"),
    )

    predicate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_predicate.id"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    value_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    datatype: Mapped[str | None] = mapped_column(Text)
    cardinality: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Cardinality.MANY.value
    )
    is_symmetric: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_transitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inverse_predicate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_predicate.id"),
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
    )


class OntologyPredicateDomain(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Allowed domain class for a predicate revision."""

    __tablename__ = "ontology_predicate_domain"
    __table_args__ = (
        UniqueConstraint(
            "predicate_revision_id",
            "class_id",
            name="uq_ontology_predicate_domain_revision_class",
        ),
        Index("ix_ontology_predicate_domain_revision", "predicate_revision_id"),
        Index("ix_ontology_predicate_domain_class", "class_id"),
    )

    predicate_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_predicate_revision.id"),
        nullable=False,
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_class.id"),
        nullable=False,
    )


class OntologyPredicateRange(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Allowed range class for an entity-valued predicate revision."""

    __tablename__ = "ontology_predicate_range"
    __table_args__ = (
        UniqueConstraint(
            "predicate_revision_id",
            "class_id",
            name="uq_ontology_predicate_range_revision_class",
        ),
        Index("ix_ontology_predicate_range_revision", "predicate_revision_id"),
        Index("ix_ontology_predicate_range_class", "class_id"),
    )

    predicate_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_predicate_revision.id"),
        nullable=False,
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_class.id"),
        nullable=False,
    )


class OntologyConstraint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Additional ontology constraint record."""

    __tablename__ = "ontology_constraint"
    __table_args__ = (
        CheckConstraint(
            f"constraint_type IN ({', '.join(repr(v.value) for v in ConstraintType)})",
            name="constraint_type",
        ),
        Index("ix_ontology_constraint_type", "constraint_type"),
    )

    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_namespace.id"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    constraint_type: Mapped[str] = mapped_column(String(32), nullable=False)
    expression: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text)


class OntologyAlias(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Alias targeting exactly one class or predicate."""

    __tablename__ = "ontology_alias"
    __table_args__ = (
        CheckConstraint(
            f"target_type IN ({', '.join(repr(v.value) for v in AliasTargetType)})",
            name="target_type",
        ),
        CheckConstraint(
            "("
            "target_type = 'class' AND class_id IS NOT NULL AND predicate_id IS NULL"
            ") OR ("
            "target_type = 'predicate' AND predicate_id IS NOT NULL AND class_id IS NULL"
            ")",
            name="alias_target_exclusivity",
        ),
        UniqueConstraint("namespace_id", "alias", name="uq_ontology_alias_namespace_alias"),
        Index("ix_ontology_alias_alias", "alias"),
        Index("ix_ontology_alias_class_id", "class_id"),
        Index("ix_ontology_alias_predicate_id", "predicate_id"),
    )

    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_namespace.id"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_class.id"),
    )
    predicate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_predicate.id"),
    )
