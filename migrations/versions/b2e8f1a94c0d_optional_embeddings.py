"""optional embeddings

Revision ID: b2e8f1a94c0d
Revises: ca1ee463c33b
Create Date: 2026-10-03 22:00:00.000000

Optional derived embedding table. No pgvector extension is required; vectors are
stored as JSONB float arrays so the core schema remains portable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2e8f1a94c0d"
down_revision: str | Sequence[str] | None = "ca1ee463c33b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "object_type IN ('class', 'predicate', 'entity')",
            name=op.f("ck_embedding_object_type"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_embedding_created_by_actor_id_actor"),
        ),
        sa.UniqueConstraint(
            "object_type",
            "object_id",
            "model_key",
            name="uq_embedding_object_model",
        ),
    )
    op.create_index("ix_embedding_object_type", "embedding", ["object_type"])
    op.create_index("ix_embedding_object_id", "embedding", ["object_id"])
    op.create_index("ix_embedding_model_key", "embedding", ["model_key"])


def downgrade() -> None:
    op.drop_index("ix_embedding_model_key", table_name="embedding")
    op.drop_index("ix_embedding_object_id", table_name="embedding")
    op.drop_index("ix_embedding_object_type", table_name="embedding")
    op.drop_table("embedding")
