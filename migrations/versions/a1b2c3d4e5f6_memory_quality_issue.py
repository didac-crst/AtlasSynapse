"""memory quality issue

Revision ID: a1b2c3d4e5f6
Revises: b8e2c4a91d0f
Create Date: 2026-10-08 09:50:00.000000

Residual post-write memory quality issues (not a conflict parallel table).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "b8e2c4a91d0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ISSUE_TYPES = (
    "supersession_integrity",
    "possible_duplicate_entity",
    "weak_or_missing_provenance",
)
_STATUSES = ("open", "resolved", "dismissed")
_SEVERITIES = ("info", "low", "medium", "high", "critical")
_RESOLUTIONS = (
    "auto_resolved",
    "corrected",
    "retracted",
    "superseded",
    "structural_repair",
    "confirmed_same",
    "confirmed_different",
    "unknown",
    "no_action",
)


def upgrade() -> None:
    op.create_table(
        "memory_quality_issue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("issue_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("detector_key", sa.Text(), nullable=False),
        sa.Column("detector_version", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("subject_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("object_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("statement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "related_entity_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "related_statement_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "requires_clarification",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("trigger_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_seen_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "occurrence_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("resolution", sa.String(length=64), nullable=True),
        sa.Column("resolution_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "issue_type IN (" + ", ".join(repr(v) for v in _ISSUE_TYPES) + ")",
            name=op.f("ck_memory_quality_issue_issue_type"),
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(repr(v) for v in _STATUSES) + ")",
            name=op.f("ck_memory_quality_issue_status"),
        ),
        sa.CheckConstraint(
            "severity IN (" + ", ".join(repr(v) for v in _SEVERITIES) + ")",
            name=op.f("ck_memory_quality_issue_severity"),
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ("
            + ", ".join(repr(v) for v in _RESOLUTIONS)
            + ")",
            name=op.f("ck_memory_quality_issue_resolution"),
        ),
        sa.ForeignKeyConstraint(
            ["subject_entity_id"],
            ["entity.id"],
            name=op.f("fk_memory_quality_issue_subject_entity_id_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["object_entity_id"],
            ["entity.id"],
            name=op.f("fk_memory_quality_issue_object_entity_id_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["statement.id"],
            name=op.f("fk_memory_quality_issue_statement_id_statement"),
        ),
        sa.ForeignKeyConstraint(
            ["trigger_operation_id"],
            ["operation_log.id"],
            name=op.f("fk_memory_quality_issue_trigger_operation_id_operation_log"),
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_operation_id"],
            ["operation_log.id"],
            name=op.f("fk_memory_quality_issue_last_seen_operation_id_operation_log"),
        ),
        sa.ForeignKeyConstraint(
            ["resolution_operation_id"],
            ["operation_log.id"],
            name=op.f("fk_memory_quality_issue_resolution_operation_id_operation_log"),
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_memory_quality_issue_resolved_by_actor_id_actor"),
        ),
    )
    op.create_index(
        "ix_memory_quality_issue_status", "memory_quality_issue", ["status"]
    )
    op.create_index(
        "ix_memory_quality_issue_issue_type", "memory_quality_issue", ["issue_type"]
    )
    op.create_index(
        "ix_memory_quality_issue_subject_entity_id",
        "memory_quality_issue",
        ["subject_entity_id"],
    )
    op.create_index(
        "ix_memory_quality_issue_statement_id",
        "memory_quality_issue",
        ["statement_id"],
    )
    op.create_index(
        "ix_memory_quality_issue_updated_at", "memory_quality_issue", ["updated_at"]
    )
    op.create_index(
        "ix_memory_quality_issue_fingerprint", "memory_quality_issue", ["fingerprint"]
    )
    op.create_index(
        "uq_memory_quality_issue_open_fingerprint",
        "memory_quality_issue",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_memory_quality_issue_open_fingerprint",
        table_name="memory_quality_issue",
    )
    op.drop_index("ix_memory_quality_issue_fingerprint", table_name="memory_quality_issue")
    op.drop_index("ix_memory_quality_issue_updated_at", table_name="memory_quality_issue")
    op.drop_index("ix_memory_quality_issue_statement_id", table_name="memory_quality_issue")
    op.drop_index(
        "ix_memory_quality_issue_subject_entity_id", table_name="memory_quality_issue"
    )
    op.drop_index("ix_memory_quality_issue_issue_type", table_name="memory_quality_issue")
    op.drop_index("ix_memory_quality_issue_status", table_name="memory_quality_issue")
    op.drop_table("memory_quality_issue")
