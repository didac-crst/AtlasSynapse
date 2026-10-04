"""agent feedback

Revision ID: d4b02e8f3c5a
Revises: c3a91d7e2b4f
Create Date: 2026-10-04 10:50:00.000000

Agent feedback observations for dogfooding. Separate from operation_log,
llm_call_log, and ontology_proposal.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4b02e8f3c5a"
down_revision: str | Sequence[str] | None = "c3a91d7e2b4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FEEDBACK_TYPES = (
    "error",
    "warning",
    "data_quality",
    "ontology_gap",
    "ambiguity",
    "suggestion",
    "usability",
    "performance",
    "security",
    "documentation",
    "unexpected_behavior",
    "missing_capability",
)
_SEVERITIES = ("info", "low", "medium", "high", "critical")
_STATUSES = ("open", "acknowledged", "resolved", "dismissed")


def upgrade() -> None:
    op.create_table(
        "agent_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feedback_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("statement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("fingerprint", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "feedback_type IN (" + ", ".join(repr(v) for v in _FEEDBACK_TYPES) + ")",
            name=op.f("ck_agent_feedback_feedback_type"),
        ),
        sa.CheckConstraint(
            "severity IN (" + ", ".join(repr(v) for v in _SEVERITIES) + ")",
            name=op.f("ck_agent_feedback_severity"),
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(repr(v) for v in _STATUSES) + ")",
            name=op.f("ck_agent_feedback_status"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_agent_feedback_actor_id_actor")
        ),
        sa.ForeignKeyConstraint(
            ["operation_log_id"],
            ["operation_log.id"],
            name=op.f("fk_agent_feedback_operation_log_id_operation_log"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ontology_proposal.id"],
            name=op.f("fk_agent_feedback_proposal_id_ontology_proposal"),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["entity.id"], name=op.f("fk_agent_feedback_entity_id_entity")
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["statement.id"],
            name=op.f("fk_agent_feedback_statement_id_statement"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_agent_feedback_source_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_agent_feedback_resolved_by_actor_id_actor"),
        ),
    )
    op.create_index("ix_agent_feedback_actor_id", "agent_feedback", ["actor_id"])
    op.create_index("ix_agent_feedback_feedback_type", "agent_feedback", ["feedback_type"])
    op.create_index("ix_agent_feedback_severity", "agent_feedback", ["severity"])
    op.create_index("ix_agent_feedback_status", "agent_feedback", ["status"])
    op.create_index("ix_agent_feedback_fingerprint", "agent_feedback", ["fingerprint"])
    op.create_index(
        "uq_agent_feedback_open_fingerprint",
        "agent_feedback",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND fingerprint IS NOT NULL"),
    )
    op.create_index("ix_agent_feedback_request_id", "agent_feedback", ["request_id"])
    op.create_index("ix_agent_feedback_created_at", "agent_feedback", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_feedback_created_at", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_request_id", table_name="agent_feedback")
    op.drop_index(
        "uq_agent_feedback_open_fingerprint",
        table_name="agent_feedback",
        if_exists=True,
    )
    op.drop_index("ix_agent_feedback_fingerprint", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_status", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_severity", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_feedback_type", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_actor_id", table_name="agent_feedback")
    op.drop_table("agent_feedback")
