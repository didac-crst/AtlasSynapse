"""knowledge ingestion durable working-state tables

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-10-09 10:40:00.000000

Phase B persistence substrate for governed knowledge ingestion.
Does not mutate ontology. No cascade cleanup of audit/replay rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INGESTION_MODES = ("execute", "dry_run")
_INGESTION_STATUSES = (
    "accepted",
    "extracting",
    "resolving",
    "awaiting_clarification",
    "paused",
    "committing",
    "completed",
    "failed",
)
_PAUSE_REASONS = ("budget_exhausted",)
_CANDIDATE_KINDS = ("assertion", "hypothesis", "recommendation", "question")
_POLARITIES = ("positive", "negative")
_EPISTEMIC = ("active", "rejected", "superseded", "open", "answered")
_DERIVATIONS = ("explicit", "normalized", "inferred")
_CANDIDATE_STATES = (
    "extracted",
    "actionable",
    "blocked",
    "resolved_commit_eligible",
    "committed",
    "discarded",
    "failed",
)
_DEPENDENCY_KINDS = (
    "requires_resolution",
    "requires_commit",
    "same_subject",
    "generic",
)
_CLARIFICATION_KINDS = ("identity", "ontology", "package_local", "policy")
_CLARIFICATION_STATUSES = ("open", "answered", "cancelled", "superseded")
_EFFECT_TYPES = ("create_entity", "assert_statement", "add_evidence")
_EFFECT_STATUSES = ("applied", "failed")


def upgrade() -> None:
    op.create_table(
        "knowledge_ingestion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_content_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column("ontology_revision_marker", sa.Text(), nullable=False),
        sa.Column("extraction_model", sa.Text(), nullable=True),
        sa.Column("extraction_prompt_version", sa.Text(), nullable=True),
        sa.Column(
            "budgets_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "stats_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("pause_reason", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "mode IN (" + ", ".join(repr(v) for v in _INGESTION_MODES) + ")",
            name=op.f("ck_knowledge_ingestion_mode"),
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(repr(v) for v in _INGESTION_STATUSES) + ")",
            name=op.f("ck_knowledge_ingestion_status"),
        ),
        sa.CheckConstraint(
            "pause_reason IS NULL OR pause_reason IN ("
            + ", ".join(repr(v) for v in _PAUSE_REASONS)
            + ")",
            name=op.f("ck_knowledge_ingestion_pause_reason"),
        ),
        sa.CheckConstraint(
            "NOT (mode = 'dry_run' AND status = 'committing')",
            name=op.f("ck_knowledge_ingestion_dry_run_never_committing"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["actor.id"],
            name=op.f("fk_knowledge_ingestion_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_knowledge_ingestion_source_id_source"),
        ),
        sa.ForeignKeyConstraint(
            ["source_content_revision_id"],
            ["source_content_revision.id"],
            name=op.f("fk_knowledge_ingestion_source_content_revision_id"),
        ),
        sa.ForeignKeyConstraint(
            ["document_entity_id"],
            ["entity.id"],
            name=op.f("fk_knowledge_ingestion_document_entity_id_entity"),
        ),
        sa.UniqueConstraint(
            "actor_id",
            "idempotency_key",
            name="uq_knowledge_ingestion_actor_idempotency_key",
        ),
    )
    op.create_index("ix_knowledge_ingestion_status", "knowledge_ingestion", ["status"])
    op.create_index("ix_knowledge_ingestion_actor_id", "knowledge_ingestion", ["actor_id"])
    op.create_index("ix_knowledge_ingestion_source_id", "knowledge_ingestion", ["source_id"])
    op.create_index("ix_knowledge_ingestion_request_id", "knowledge_ingestion", ["request_id"])
    op.create_index("ix_knowledge_ingestion_mode", "knowledge_ingestion", ["mode"])

    op.create_table(
        "knowledge_candidate",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ingestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("polarity", sa.String(length=16), nullable=False),
        sa.Column("epistemic_status", sa.String(length=32), nullable=False),
        sa.Column("derivation", sa.String(length=32), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=True),
        sa.Column(
            "claim_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source_span",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source_context_path",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "blockers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "resolution_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("committed_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "committed_statement_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
            "kind IN (" + ", ".join(repr(v) for v in _CANDIDATE_KINDS) + ")",
            name=op.f("ck_knowledge_candidate_kind"),
        ),
        sa.CheckConstraint(
            "polarity IN (" + ", ".join(repr(v) for v in _POLARITIES) + ")",
            name=op.f("ck_knowledge_candidate_polarity"),
        ),
        sa.CheckConstraint(
            "epistemic_status IN (" + ", ".join(repr(v) for v in _EPISTEMIC) + ")",
            name=op.f("ck_knowledge_candidate_epistemic_status"),
        ),
        sa.CheckConstraint(
            "derivation IN (" + ", ".join(repr(v) for v in _DERIVATIONS) + ")",
            name=op.f("ck_knowledge_candidate_derivation"),
        ),
        sa.CheckConstraint(
            "state IN (" + ", ".join(repr(v) for v in _CANDIDATE_STATES) + ")",
            name=op.f("ck_knowledge_candidate_state"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["knowledge_ingestion.id"],
            name=op.f("fk_knowledge_candidate_ingestion_id_knowledge_ingestion"),
        ),
        sa.ForeignKeyConstraint(
            ["committed_entity_id"],
            ["entity.id"],
            name=op.f("fk_knowledge_candidate_committed_entity_id_entity"),
        ),
        sa.UniqueConstraint(
            "ingestion_id",
            "candidate_key",
            name="uq_knowledge_candidate_ingestion_candidate_key",
        ),
    )
    op.create_index("ix_knowledge_candidate_ingestion_id", "knowledge_candidate", ["ingestion_id"])
    op.create_index("ix_knowledge_candidate_state", "knowledge_candidate", ["state"])
    op.create_index("ix_knowledge_candidate_kind", "knowledge_candidate", ["kind"])
    op.create_index(
        "ix_knowledge_candidate_epistemic_status",
        "knowledge_candidate",
        ["epistemic_status"],
    )

    op.create_table(
        "knowledge_candidate_dependency",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("parent_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dependency_kind", sa.String(length=32), nullable=False),
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
            "dependency_kind IN (" + ", ".join(repr(v) for v in _DEPENDENCY_KINDS) + ")",
            name=op.f("ck_knowledge_candidate_dependency_dependency_kind"),
        ),
        sa.CheckConstraint(
            "parent_candidate_id <> child_candidate_id",
            name=op.f("ck_knowledge_candidate_dependency_no_self_dependency"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_candidate_id"],
            ["knowledge_candidate.id"],
            name=op.f("fk_knowledge_candidate_dependency_parent_candidate_id"),
        ),
        sa.ForeignKeyConstraint(
            ["child_candidate_id"],
            ["knowledge_candidate.id"],
            name=op.f("fk_knowledge_candidate_dependency_child_candidate_id"),
        ),
        sa.UniqueConstraint(
            "parent_candidate_id",
            "child_candidate_id",
            "dependency_kind",
            name="uq_knowledge_candidate_dependency_edge",
        ),
    )
    op.create_index(
        "ix_knowledge_candidate_dependency_parent",
        "knowledge_candidate_dependency",
        ["parent_candidate_id"],
    )
    op.create_index(
        "ix_knowledge_candidate_dependency_child",
        "knowledge_candidate_dependency",
        ["child_candidate_id"],
    )

    op.create_table(
        "knowledge_ingestion_clarification",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ingestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("clarification_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "question_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("answer_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "impact_blocked_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("write_clarification_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "ontology_clarification_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
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
            "clarification_kind IN (" + ", ".join(repr(v) for v in _CLARIFICATION_KINDS) + ")",
            name=op.f("ck_knowledge_ingestion_clarification_clarification_kind"),
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(repr(v) for v in _CLARIFICATION_STATUSES) + ")",
            name=op.f("ck_knowledge_ingestion_clarification_status"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["knowledge_ingestion.id"],
            name=op.f("fk_knowledge_ingestion_clarification_ingestion_id"),
        ),
        sa.ForeignKeyConstraint(
            ["root_candidate_id"],
            ["knowledge_candidate.id"],
            name=op.f("fk_knowledge_ingestion_clarification_root_candidate_id"),
        ),
        sa.ForeignKeyConstraint(
            ["write_clarification_request_id"],
            ["write_clarification_request.id"],
            name=op.f("fk_ki_clarification_write_clarification_request_id"),
        ),
        sa.ForeignKeyConstraint(
            ["ontology_clarification_request_id"],
            ["ontology_semantic_clarification_request.id"],
            name=op.f("fk_ki_clarification_ontology_clarification_request_id"),
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_clarification_ingestion_id",
        "knowledge_ingestion_clarification",
        ["ingestion_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_clarification_status",
        "knowledge_ingestion_clarification",
        ["status"],
    )
    op.create_index(
        "ix_ki_clarification_write_clarification_request_id",
        "knowledge_ingestion_clarification",
        ["write_clarification_request_id"],
    )

    op.create_table(
        "knowledge_ingestion_effect",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ingestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effect_key", sa.Text(), nullable=False),
        sa.Column("effect_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("statement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            "effect_type IN (" + ", ".join(repr(v) for v in _EFFECT_TYPES) + ")",
            name=op.f("ck_knowledge_ingestion_effect_effect_type"),
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(repr(v) for v in _EFFECT_STATUSES) + ")",
            name=op.f("ck_knowledge_ingestion_effect_status"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["knowledge_ingestion.id"],
            name=op.f("fk_knowledge_ingestion_effect_ingestion_id_knowledge_ingestion"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["knowledge_candidate.id"],
            name=op.f("fk_knowledge_ingestion_effect_candidate_id_knowledge_candidate"),
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operation_log.id"],
            name=op.f("fk_knowledge_ingestion_effect_operation_id_operation_log"),
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["statement.id"],
            name=op.f("fk_knowledge_ingestion_effect_statement_id_statement"),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entity.id"],
            name=op.f("fk_knowledge_ingestion_effect_entity_id_entity"),
        ),
        sa.UniqueConstraint(
            "ingestion_id",
            "candidate_id",
            "effect_key",
            name="uq_knowledge_ingestion_effect_key",
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_effect_ingestion_id",
        "knowledge_ingestion_effect",
        ["ingestion_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_effect_candidate_id",
        "knowledge_ingestion_effect",
        ["candidate_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_effect_status",
        "knowledge_ingestion_effect",
        ["status"],
    )
    op.create_index(
        "ix_knowledge_ingestion_effect_idempotency_key",
        "knowledge_ingestion_effect",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_ingestion_effect_idempotency_key",
        table_name="knowledge_ingestion_effect",
    )
    op.drop_index(
        "ix_knowledge_ingestion_effect_status",
        table_name="knowledge_ingestion_effect",
    )
    op.drop_index(
        "ix_knowledge_ingestion_effect_candidate_id",
        table_name="knowledge_ingestion_effect",
    )
    op.drop_index(
        "ix_knowledge_ingestion_effect_ingestion_id",
        table_name="knowledge_ingestion_effect",
    )
    op.drop_table("knowledge_ingestion_effect")

    op.drop_index(
        "ix_ki_clarification_write_clarification_request_id",
        table_name="knowledge_ingestion_clarification",
    )
    op.drop_index(
        "ix_knowledge_ingestion_clarification_status",
        table_name="knowledge_ingestion_clarification",
    )
    op.drop_index(
        "ix_knowledge_ingestion_clarification_ingestion_id",
        table_name="knowledge_ingestion_clarification",
    )
    op.drop_table("knowledge_ingestion_clarification")

    op.drop_index(
        "ix_knowledge_candidate_dependency_child",
        table_name="knowledge_candidate_dependency",
    )
    op.drop_index(
        "ix_knowledge_candidate_dependency_parent",
        table_name="knowledge_candidate_dependency",
    )
    op.drop_table("knowledge_candidate_dependency")

    op.drop_index(
        "ix_knowledge_candidate_epistemic_status",
        table_name="knowledge_candidate",
    )
    op.drop_index("ix_knowledge_candidate_kind", table_name="knowledge_candidate")
    op.drop_index("ix_knowledge_candidate_state", table_name="knowledge_candidate")
    op.drop_index("ix_knowledge_candidate_ingestion_id", table_name="knowledge_candidate")
    op.drop_table("knowledge_candidate")

    op.drop_index("ix_knowledge_ingestion_mode", table_name="knowledge_ingestion")
    op.drop_index("ix_knowledge_ingestion_request_id", table_name="knowledge_ingestion")
    op.drop_index("ix_knowledge_ingestion_source_id", table_name="knowledge_ingestion")
    op.drop_index("ix_knowledge_ingestion_actor_id", table_name="knowledge_ingestion")
    op.drop_index("ix_knowledge_ingestion_status", table_name="knowledge_ingestion")
    op.drop_table("knowledge_ingestion")
