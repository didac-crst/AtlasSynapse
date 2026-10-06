"""core agent and place under thing

Revision ID: e5c13a7b9d2f
Revises: d4b02e8f3c5a
Create Date: 2026-10-04 16:50:00.000000

Fold Agent→Thing and Place→Thing into the core ontology inheritance so
Person/Organization satisfy Thing-domain/range predicates such as relatedTo
without requiring the rich-event seed extension.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c13a7b9d2f"
down_revision: str | Sequence[str] | None = "d4b02e8f3c5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_NS = uuid.UUID("00000000-0000-4000-8000-000000000001")
_CORE_NS = "core"
_PARENT_LINKS: tuple[tuple[str, str], ...] = (
    ("Agent", "Thing"),
    ("Place", "Thing"),
)


def _sid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NS, ":".join(parts))


def upgrade() -> None:
    conn = op.get_bind()
    for child_key, parent_key in _PARENT_LINKS:
        child_id = conn.execute(
            sa.text(
                "SELECT c.id FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :ns AND c.key = :key"
            ),
            {"ns": _CORE_NS, "key": child_key},
        ).scalar_one()
        parent_id = conn.execute(
            sa.text(
                "SELECT c.id FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :ns AND c.key = :key"
            ),
            {"ns": _CORE_NS, "key": parent_key},
        ).scalar_one()
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM ontology_class_parent "
                "WHERE child_class_id = :child AND parent_class_id = :parent"
            ),
            {"child": child_id, "parent": parent_id},
        ).scalar()
        if exists is not None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO ontology_class_parent "
                "(id, child_class_id, parent_class_id, created_at) "
                "VALUES (:id, :child, :parent, NOW())"
            ),
            {
                "id": _sid("class_parent", _CORE_NS, child_key, parent_key),
                "child": child_id,
                "parent": parent_id,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for child_key, parent_key in _PARENT_LINKS:
        conn.execute(
            sa.text("DELETE FROM ontology_class_parent WHERE id = :id"),
            {"id": _sid("class_parent", _CORE_NS, child_key, parent_key)},
        )
