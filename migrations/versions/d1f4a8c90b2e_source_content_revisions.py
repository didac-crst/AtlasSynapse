"""source content revisions

Revision ID: d1f4a8c90b2e
Revises: c8d5f3b02e1a
Create Date: 2026-10-06 17:20:00.000000

Add immutable source content revisions and optional source→entity link
(many sources may map to one Document entity). Seed authoredBy and
publicationContext core predicates when missing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f4a8c90b2e"
down_revision: str | Sequence[str] | None = "c8d5f3b02e1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_NS = uuid.UUID("00000000-0000-4000-8000-000000000001")
_CORE_NS = "core"


def _sid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NS, ":".join(parts))


def _ensure_predicate(
    conn: sa.Connection,
    *,
    key: str,
    value_kind: str,
    cardinality: str,
    domain_keys: tuple[str, ...],
    range_keys: tuple[str, ...],
) -> None:
    ns_id = conn.execute(
        sa.text("SELECT id FROM ontology_namespace WHERE key = :ns"),
        {"ns": _CORE_NS},
    ).scalar()
    if ns_id is None:
        return
    actor_id = conn.execute(
        sa.text("SELECT id FROM actor WHERE name = 'system' ORDER BY created_at LIMIT 1")
    ).scalar()
    if actor_id is None:
        return

    pred_id = conn.execute(
        sa.text(
            "SELECT p.id FROM ontology_predicate p "
            "WHERE p.namespace_id = :ns AND p.key = :key"
        ),
        {"ns": ns_id, "key": key},
    ).scalar()
    if pred_id is None:
        pred_id = _sid("predicate", _CORE_NS, key)
        rev_id = _sid("predicate_revision", _CORE_NS, key, "1")
        conn.execute(
            sa.text(
                "INSERT INTO ontology_predicate "
                "(id, namespace_id, key, current_revision_id, is_deprecated, created_at, updated_at) "
                "VALUES (:id, :ns, :key, NULL, false, NOW(), NOW())"
            ),
            {"id": pred_id, "ns": ns_id, "key": key},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ontology_predicate_revision "
                "(id, predicate_id, revision_number, label, description, value_kind, datatype, "
                "cardinality, is_symmetric, is_transitive, metadata, created_by_actor_id, created_at) "
                "VALUES (:id, :predicate_id, 1, :label, :description, :value_kind, NULL, "
                ":cardinality, false, false, '{}'::jsonb, :actor_id, NOW())"
            ),
            {
                "id": rev_id,
                "predicate_id": pred_id,
                "label": key,
                "description": f"Core ontology predicate {key}.",
                "value_kind": value_kind,
                "cardinality": cardinality,
                "actor_id": actor_id,
            },
        )
        conn.execute(
            sa.text(
                "UPDATE ontology_predicate SET current_revision_id = :rev WHERE id = :id"
            ),
            {"rev": rev_id, "id": pred_id},
        )
    else:
        rev_id = conn.execute(
            sa.text("SELECT current_revision_id FROM ontology_predicate WHERE id = :id"),
            {"id": pred_id},
        ).scalar()
        if rev_id is None:
            return

    for class_key in domain_keys:
        class_id = conn.execute(
            sa.text(
                "SELECT c.id FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :ns AND c.key = :key"
            ),
            {"ns": _CORE_NS, "key": class_key},
        ).scalar()
        if class_id is None:
            continue
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM ontology_predicate_domain "
                "WHERE predicate_revision_id = :rev AND class_id = :class_id"
            ),
            {"rev": rev_id, "class_id": class_id},
        ).scalar()
        if exists is None:
            conn.execute(
                sa.text(
                    "INSERT INTO ontology_predicate_domain "
                    "(id, predicate_revision_id, class_id) "
                    "VALUES (:id, :rev, :class_id)"
                ),
                {
                    "id": _sid("predicate_domain", _CORE_NS, key, class_key),
                    "rev": rev_id,
                    "class_id": class_id,
                },
            )

    for class_key in range_keys:
        class_id = conn.execute(
            sa.text(
                "SELECT c.id FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :ns AND c.key = :key"
            ),
            {"ns": _CORE_NS, "key": class_key},
        ).scalar()
        if class_id is None:
            continue
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM ontology_predicate_range "
                "WHERE predicate_revision_id = :rev AND class_id = :class_id"
            ),
            {"rev": rev_id, "class_id": class_id},
        ).scalar()
        if exists is None:
            conn.execute(
                sa.text(
                    "INSERT INTO ontology_predicate_range "
                    "(id, predicate_revision_id, class_id) "
                    "VALUES (:id, :rev, :class_id)"
                ),
                {
                    "id": _sid("predicate_range", _CORE_NS, key, class_key),
                    "rev": rev_id,
                    "class_id": class_id,
                },
            )


def upgrade() -> None:
    op.add_column(
        "source",
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_source_entity_id_entity",
        "source",
        "entity",
        ["entity_id"],
        ["id"],
    )
    op.create_index("ix_source_entity_id", "source", ["entity_id"])

    op.create_table(
        "source_content_revision",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("canonical_content", sa.Text(), nullable=False),
        sa.Column("canonical_format", sa.String(length=32), nullable=False),
        sa.Column("canonical_content_hash", sa.Text(), nullable=False),
        sa.Column("original_content", sa.Text(), nullable=True),
        sa.Column("original_format", sa.String(length=32), nullable=True),
        sa.Column("original_content_hash", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["source_id"], ["source.id"]),
        sa.ForeignKeyConstraint(["created_by_actor_id"], ["actor.id"]),
        sa.UniqueConstraint(
            "source_id",
            "canonical_content_hash",
            name="uq_source_content_revision_source_canonical_hash",
        ),
        sa.UniqueConstraint(
            "source_id",
            "revision_number",
            name="uq_source_content_revision_source_revision_number",
        ),
        sa.CheckConstraint(
            "canonical_format IN ('markdown', 'text', 'html')",
            name="ck_source_content_revision_canonical_format",
        ),
        sa.CheckConstraint(
            "original_format IS NULL OR original_format IN ('markdown', 'text', 'html')",
            name="ck_source_content_revision_original_format",
        ),
    )
    op.create_index(
        "ix_source_content_revision_source_id",
        "source_content_revision",
        ["source_id"],
    )
    op.create_index(
        "ix_source_content_revision_canonical_content_hash",
        "source_content_revision",
        ["canonical_content_hash"],
    )

    conn = op.get_bind()
    _ensure_predicate(
        conn,
        key="authoredBy",
        value_kind="entity",
        cardinality="many",
        domain_keys=("Document",),
        range_keys=("Person",),
    )
    _ensure_predicate(
        conn,
        key="publicationContext",
        value_kind="entity",
        cardinality="many",
        domain_keys=("Document",),
        range_keys=("Organization",),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_content_revision_canonical_content_hash",
        table_name="source_content_revision",
    )
    op.drop_index("ix_source_content_revision_source_id", table_name="source_content_revision")
    op.drop_table("source_content_revision")
    op.drop_index("ix_source_entity_id", table_name="source")
    op.drop_constraint("fk_source_entity_id_entity", "source", type_="foreignkey")
    op.drop_column("source", "entity_id")
