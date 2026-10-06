"""semantic review lineage

Revision ID: f1a82c4d9e7b
Revises: e5c13a7b9d2f
Create Date: 2026-10-04 20:40:00.000000

Append-only semantic review and challenge tables, plus effective review pointer
on ontology_proposal. Gate results remain historical; current semantic decision
is derived from effective_semantic_review_id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a82c4d9e7b"
down_revision: str | Sequence[str] | None = "e5c13a7b9d2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ontology_semantic_challenge",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("against_review_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("challenge_reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "proposed_revision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('accepted_for_review', 'rejected_as_insubstantive', 'reviewed')",
            name=op.f("ck_ontology_semantic_challenge_status"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ontology_proposal.id"],
            name=op.f("fk_ontology_semantic_challenge_proposal_id_ontology_proposal"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_ontology_semantic_challenge_created_by_actor_id_actor"),
        ),
    )
    op.create_index(
        "ix_ontology_semantic_challenge_proposal_id",
        "ontology_semantic_challenge",
        ["proposal_id"],
        unique=False,
    )

    op.create_table(
        "ontology_semantic_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_stage", sa.String(length=32), nullable=False),
        sa.Column("previous_review_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_template_version", sa.Text(), nullable=False),
        sa.Column("context_builder_version", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column(
            "context_concept_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "related_existing_concepts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "recommended_actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "context_sufficient", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("challengeable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("previous_decision", sa.String(length=32), nullable=True),
        sa.Column("decision_changed", sa.Boolean(), nullable=True),
        sa.Column("llm_call_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "details",
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
            "review_stage IN ('initial', 'challenge')",
            name=op.f("ck_ontology_semantic_review_review_stage"),
        ),
        sa.CheckConstraint(
            "decision IN ("
            "'approve', 'reject', 'manual_review', 'reuse_existing', 'uphold_rejection'"
            ")",
            name=op.f("ck_ontology_semantic_review_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ontology_proposal.id"],
            name=op.f("fk_ontology_semantic_review_proposal_id_ontology_proposal"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_review_id"],
            ["ontology_semantic_review.id"],
            name=op.f("fk_ontology_semantic_review_previous_review_id"),
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["ontology_semantic_challenge.id"],
            name=op.f("fk_ontology_semantic_review_challenge_id"),
        ),
        sa.ForeignKeyConstraint(
            ["llm_call_log_id"],
            ["llm_call_log.id"],
            name=op.f("fk_ontology_semantic_review_llm_call_log_id"),
        ),
    )
    op.create_index(
        "ix_ontology_semantic_review_proposal_id",
        "ontology_semantic_review",
        ["proposal_id"],
        unique=False,
    )
    op.create_index(
        "ix_ontology_semantic_review_created_at",
        "ontology_semantic_review",
        ["created_at"],
        unique=False,
    )

    op.create_foreign_key(
        op.f("fk_ontology_semantic_challenge_against_review_id"),
        "ontology_semantic_challenge",
        "ontology_semantic_review",
        ["against_review_id"],
        ["id"],
    )

    op.add_column(
        "ontology_proposal",
        sa.Column("effective_semantic_review_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_ontology_proposal_effective_semantic_review_id"),
        "ontology_proposal",
        "ontology_semantic_review",
        ["effective_semantic_review_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_ontology_proposal_effective_semantic_review_id"),
        "ontology_proposal",
        type_="foreignkey",
    )
    op.drop_column("ontology_proposal", "effective_semantic_review_id")
    op.drop_constraint(
        op.f("fk_ontology_semantic_challenge_against_review_id"),
        "ontology_semantic_challenge",
        type_="foreignkey",
    )
    op.drop_index("ix_ontology_semantic_review_created_at", table_name="ontology_semantic_review")
    op.drop_index("ix_ontology_semantic_review_proposal_id", table_name="ontology_semantic_review")
    op.drop_table("ontology_semantic_review")
    op.drop_index(
        "ix_ontology_semantic_challenge_proposal_id", table_name="ontology_semantic_challenge"
    )
    op.drop_table("ontology_semantic_challenge")
