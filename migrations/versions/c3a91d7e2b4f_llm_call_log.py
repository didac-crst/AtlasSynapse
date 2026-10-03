"""llm call log

Revision ID: c3a91d7e2b4f
Revises: b2e8f1a94c0d
Create Date: 2026-10-03 22:45:00.000000

Durable LLM call observability table (begin/complete per row). Separate from
operation_log.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a91d7e2b4f"
down_revision: str | Sequence[str] | None = "b2e8f1a94c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_call_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_amount", sa.Numeric(18, 8), nullable=True),
        sa.Column("cost_currency", sa.String(length=16), nullable=True),
        sa.Column("cost_status", sa.String(length=32), nullable=False),
        sa.Column("pricing_version", sa.Text(), nullable=True),
        sa.Column("pricing_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'unavailable', 'manual_review')",
            name=op.f("ck_llm_call_log_status"),
        ),
        sa.CheckConstraint(
            "cost_status IN ('estimated', 'provider_reported', 'unknown')",
            name=op.f("ck_llm_call_log_cost_status"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["actor.id"],
            name=op.f("fk_llm_call_log_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["operation_log_id"],
            ["operation_log.id"],
            name=op.f("fk_llm_call_log_operation_log_id_operation_log"),
        ),
    )
    op.create_index("ix_llm_call_log_actor_id", "llm_call_log", ["actor_id"])
    op.create_index("ix_llm_call_log_operation_log_id", "llm_call_log", ["operation_log_id"])
    op.create_index("ix_llm_call_log_request_id", "llm_call_log", ["request_id"])
    op.create_index("ix_llm_call_log_trace_id", "llm_call_log", ["trace_id"])
    op.create_index("ix_llm_call_log_provider", "llm_call_log", ["provider"])
    op.create_index("ix_llm_call_log_purpose", "llm_call_log", ["purpose"])
    op.create_index("ix_llm_call_log_status", "llm_call_log", ["status"])
    op.create_index("ix_llm_call_log_started_at", "llm_call_log", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_call_log_started_at", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_status", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_purpose", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_provider", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_trace_id", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_request_id", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_operation_log_id", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_actor_id", table_name="llm_call_log")
    op.drop_table("llm_call_log")
