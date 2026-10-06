"""semantic clarification requests

Revision ID: b7c4e2a91d0f
Revises: a3b91e2f8a4c
Create Date: 2026-10-04 21:25:00.000000

Referential clarification asks issued when semantic review cannot decide
whether a proposal is new vs overlapping an existing concept.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c4e2a91d0f"
down_revision: str | Sequence[str] | None = "a3b91e2f8a4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Pass the logical constraint name; naming convention prefixes ck_<table>_.
    op.drop_constraint("review_stage", "ontology_semantic_review", type_="check")
    op.create_check_constraint(
        "review_stage",
        "ontology_semantic_review",
        "review_stage IN ('initial', 'challenge', 'clarification')",
    )

    op.create_table(
        "ontology_semantic_clarification_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "required_clarification",
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
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("answered_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resulting_review_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["proposal_id"], ["ontology_proposal.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["ontology_semantic_review.id"]),
        sa.ForeignKeyConstraint(["resulting_review_id"], ["ontology_semantic_review.id"]),
        sa.ForeignKeyConstraint(["answered_by_actor_id"], ["actor.id"]),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'superseded', 'resolved')",
            name="status",
        ),
    )
    op.create_index(
        "ix_ontology_semantic_clarification_request_proposal_id",
        "ontology_semantic_clarification_request",
        ["proposal_id"],
    )
    op.create_index(
        "ix_ontology_semantic_clarification_request_review_id",
        "ontology_semantic_clarification_request",
        ["review_id"],
    )
    op.create_index(
        "ix_ontology_semantic_clarification_request_status",
        "ontology_semantic_clarification_request",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ontology_semantic_clarification_request_status",
        table_name="ontology_semantic_clarification_request",
    )
    op.drop_index(
        "ix_ontology_semantic_clarification_request_review_id",
        table_name="ontology_semantic_clarification_request",
    )
    op.drop_index(
        "ix_ontology_semantic_clarification_request_proposal_id",
        table_name="ontology_semantic_clarification_request",
    )
    op.drop_table("ontology_semantic_clarification_request")

    op.drop_constraint("review_stage", "ontology_semantic_review", type_="check")
    op.create_check_constraint(
        "review_stage",
        "ontology_semantic_review",
        "review_stage IN ('initial', 'challenge')",
    )
