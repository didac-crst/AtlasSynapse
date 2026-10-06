"""write clarification requests

Revision ID: b8e2c4a91d0f
Revises: a9c4e1f72b8d
Create Date: 2026-10-06 21:55:00.000000

Ephemeral control-plane handles for identity ambiguity on statement writes.
Not knowledge: TTL + GC; survives dry-run savepoint rollback.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8e2c4a91d0f"
down_revision: str | Sequence[str] | None = "a9c4e1f72b8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "write_clarification_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_name", sa.Text(), nullable=False),
        sa.Column("operation_mode", sa.String(length=16), nullable=False),
        sa.Column("original_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ambiguous_path", sa.String(length=16), nullable=False),
        sa.Column("frozen_request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "identity_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "candidate_entity_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "supersedes_clarification_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("chosen_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("answered_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resulting_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["actor.id"]),
        sa.ForeignKeyConstraint(["answered_by_actor_id"], ["actor.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_clarification_request_id"],
            ["write_clarification_request.id"],
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'expired', 'superseded')",
            name="status",
        ),
        sa.CheckConstraint(
            "operation_mode IN ('execute', 'dry_run')",
            name="operation_mode",
        ),
        sa.CheckConstraint(
            "ambiguous_path IN ('subject', 'object')",
            name="ambiguous_path",
        ),
    )
    op.create_index(
        "ix_write_clarification_request_status",
        "write_clarification_request",
        ["status"],
    )
    op.create_index(
        "ix_write_clarification_request_actor_id",
        "write_clarification_request",
        ["actor_id"],
    )
    op.create_index(
        "ix_write_clarification_request_expires_at",
        "write_clarification_request",
        ["expires_at"],
    )
    op.create_index(
        "ix_write_clarification_request_original_request_id",
        "write_clarification_request",
        ["original_request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_write_clarification_request_original_request_id",
        table_name="write_clarification_request",
    )
    op.drop_index(
        "ix_write_clarification_request_expires_at",
        table_name="write_clarification_request",
    )
    op.drop_index(
        "ix_write_clarification_request_actor_id",
        table_name="write_clarification_request",
    )
    op.drop_index(
        "ix_write_clarification_request_status",
        table_name="write_clarification_request",
    )
    op.drop_table("write_clarification_request")
