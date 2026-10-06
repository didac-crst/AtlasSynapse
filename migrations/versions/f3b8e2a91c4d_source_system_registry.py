"""source system registry and canonical source identity

Revision ID: f3b8e2a91c4d
Revises: e2a7c1d84b3f
Create Date: 2026-10-06 18:20:00.000000

Add source-system registry/aliases, persist canonical_source_system on source,
flag existing identity collisions explicitly (no silent merge/delete), and
enforce uniqueness for non-conflicted (canonical, external_id) pairs.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3b8e2a91c4d"
down_revision: str | Sequence[str] | None = "e2a7c1d84b3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_NS = uuid.UUID("00000000-0000-4000-8000-000000000002")

_CONFLUENCE_ALIASES = (
    "confluence",
    "Confluence",
    "Airbus Confluence",
    "atlassian_confluence",
    "Atlassian Confluence",
)


def _sid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NS, ":".join(parts))


def _normalize_key(raw: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower())
    return collapsed.strip("_")


def upgrade() -> None:
    op.create_table(
        "source_system_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("canonical_key", name="uq_source_system_registry_canonical_key"),
    )
    op.create_index(
        "ix_source_system_registry_canonical_key",
        "source_system_registry",
        ["canonical_key"],
    )

    op.create_table(
        "source_system_alias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("registry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["registry_id"],
            ["source_system_registry.id"],
            name="fk_source_system_alias_registry_id",
        ),
        sa.UniqueConstraint(
            "normalized_alias",
            name="uq_source_system_alias_normalized_alias",
        ),
    )
    op.create_index(
        "ix_source_system_alias_registry_id",
        "source_system_alias",
        ["registry_id"],
    )

    op.create_table(
        "source_identity_conflict",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("canonical_source_system", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column(
            "source_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_source_identity_conflict_status",
        ),
        sa.UniqueConstraint(
            "canonical_source_system",
            "external_id",
            name="uq_source_identity_conflict_canonical_external",
        ),
    )

    op.add_column(
        "source",
        sa.Column("canonical_source_system", sa.Text(), nullable=True),
    )
    op.add_column(
        "source",
        sa.Column(
            "identity_conflict",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_source_canonical_source_system",
        "source",
        ["canonical_source_system"],
    )

    conn = op.get_bind()

    # Seed confluence registry + aliases.
    registry_id = _sid("source_system", "confluence")
    conn.execute(
        sa.text(
            "INSERT INTO source_system_registry "
            "(id, canonical_key, label, description, created_at, updated_at) "
            "VALUES (:id, 'confluence', 'Confluence', "
            " 'Atlassian Confluence wiki / pages', NOW(), NOW())"
        ),
        {"id": registry_id},
    )
    for alias in _CONFLUENCE_ALIASES:
        conn.execute(
            sa.text(
                "INSERT INTO source_system_alias "
                "(id, registry_id, alias, normalized_alias, created_at) "
                "VALUES (:id, :registry_id, :alias, :normalized, NOW()) "
                "ON CONFLICT (normalized_alias) DO NOTHING"
            ),
            {
                "id": _sid("source_system_alias", "confluence", _normalize_key(alias)),
                "registry_id": registry_id,
                "alias": alias,
                "normalized": _normalize_key(alias),
            },
        )

    # Backfill canonical_source_system from registry aliases, else provisional key.
    rows = conn.execute(
        sa.text("SELECT id, source_system FROM source WHERE source_system IS NOT NULL")
    ).mappings().all()
    alias_map = {
        row[0]: row[1]
        for row in conn.execute(
            sa.text(
                "SELECT a.normalized_alias, r.canonical_key "
                "FROM source_system_alias a "
                "JOIN source_system_registry r ON r.id = a.registry_id"
            )
        ).all()
    }
    for row in rows:
        raw = row["source_system"]
        normalized = _normalize_key(raw)
        canonical = alias_map.get(normalized, normalized)
        conn.execute(
            sa.text(
                "UPDATE source SET canonical_source_system = :canonical WHERE id = :id"
            ),
            {"canonical": canonical, "id": row["id"]},
        )

    # Flag identity collisions explicitly — do not merge or delete.
    import json

    conflicts = conn.execute(
        sa.text(
            "SELECT canonical_source_system, external_id, "
            "       array_agg(id::text ORDER BY created_at, id) AS source_ids "
            "FROM source "
            "WHERE canonical_source_system IS NOT NULL "
            "  AND external_id IS NOT NULL "
            "GROUP BY canonical_source_system, external_id "
            "HAVING COUNT(*) > 1"
        )
    ).mappings().all()
    for conflict in conflicts:
        source_ids = list(conflict["source_ids"])
        conn.execute(
            sa.text(
                "UPDATE source SET identity_conflict = true "
                "WHERE canonical_source_system = :canonical "
                "  AND external_id = :external_id"
            ),
            {
                "canonical": conflict["canonical_source_system"],
                "external_id": conflict["external_id"],
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO source_identity_conflict "
                "(id, canonical_source_system, external_id, source_ids, status, notes, created_at) "
                "VALUES (:id, :canonical, :external_id, CAST(:source_ids AS jsonb), "
                "'open', :notes, NOW())"
            ),
            {
                "id": uuid.uuid4(),
                "canonical": conflict["canonical_source_system"],
                "external_id": conflict["external_id"],
                "source_ids": json.dumps(source_ids),
                "notes": (
                    "Detected during f3b8e2a91c4d backfill; "
                    "manual resolution required before unique identity can bind."
                ),
            },
        )

    op.create_index(
        "uq_source_canonical_external_id",
        "source",
        ["canonical_source_system", "external_id"],
        unique=True,
        postgresql_where=sa.text(
            "canonical_source_system IS NOT NULL "
            "AND external_id IS NOT NULL "
            "AND identity_conflict = false"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_source_canonical_external_id", table_name="source")
    op.drop_index("ix_source_canonical_source_system", table_name="source")
    op.drop_column("source", "identity_conflict")
    op.drop_column("source", "canonical_source_system")
    op.drop_table("source_identity_conflict")
    op.drop_index("ix_source_system_alias_registry_id", table_name="source_system_alias")
    op.drop_table("source_system_alias")
    op.drop_index(
        "ix_source_system_registry_canonical_key",
        table_name="source_system_registry",
    )
    op.drop_table("source_system_registry")
