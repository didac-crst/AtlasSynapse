"""clarification supersedes link

Revision ID: c8d5f3b02e1a
Revises: b7c4e2a91d0f
Create Date: 2026-10-04 21:30:00.000000

Track which prior open clarification a new request supersedes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8d5f3b02e1a"
down_revision: str | Sequence[str] | None = "b7c4e2a91d0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ontology_semantic_clarification_request",
        sa.Column(
            "supersedes_clarification_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_ontology_semantic_clarification_request_supersedes",
        "ontology_semantic_clarification_request",
        "ontology_semantic_clarification_request",
        ["supersedes_clarification_request_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ontology_semantic_clarification_request_supersedes",
        "ontology_semantic_clarification_request",
        type_="foreignkey",
    )
    op.drop_column(
        "ontology_semantic_clarification_request",
        "supersedes_clarification_request_id",
    )
