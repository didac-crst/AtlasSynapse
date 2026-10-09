"""knowledge ingestion clarification stable key

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-10-09 17:00:00.000000

Phase E: add clarification_key (unique per ingestion) and optional
supersedes_clarification_id self-FK. Safe for non-empty tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_ingestion_clarification",
        sa.Column("clarification_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_ingestion_clarification",
        sa.Column(
            "supersedes_clarification_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    # Backfill stable keys for any pre-Phase-E rows.
    op.execute(
        sa.text(
            "UPDATE knowledge_ingestion_clarification "
            "SET clarification_key = 'legacy:' || id::text "
            "WHERE clarification_key IS NULL"
        )
    )
    op.alter_column(
        "knowledge_ingestion_clarification",
        "clarification_key",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_knowledge_ingestion_clarification_key",
        "knowledge_ingestion_clarification",
        ["ingestion_id", "clarification_key"],
    )
    op.create_foreign_key(
        op.f("fk_ki_clarification_supersedes_clarification_id"),
        "knowledge_ingestion_clarification",
        "knowledge_ingestion_clarification",
        ["supersedes_clarification_id"],
        ["id"],
    )
    op.create_index(
        "ix_ki_clarification_clarification_key",
        "knowledge_ingestion_clarification",
        ["clarification_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ki_clarification_clarification_key",
        table_name="knowledge_ingestion_clarification",
    )
    op.drop_constraint(
        op.f("fk_ki_clarification_supersedes_clarification_id"),
        "knowledge_ingestion_clarification",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_knowledge_ingestion_clarification_key",
        "knowledge_ingestion_clarification",
        type_="unique",
    )
    op.drop_column("knowledge_ingestion_clarification", "supersedes_clarification_id")
    op.drop_column("knowledge_ingestion_clarification", "clarification_key")
