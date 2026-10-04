"""semantic review authoritative flag

Revision ID: a3b91e2f8a4c
Revises: f1a82c4d9e7b
Create Date: 2026-10-04 20:55:00.000000

Add authoritative flag so shadow-mode reviews can persist the model decision
while remaining non-binding for proposal outcomes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b91e2f8a4c"
down_revision: str | Sequence[str] | None = "f1a82c4d9e7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ontology_semantic_review",
        sa.Column(
            "authoritative",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ontology_semantic_review", "authoritative")
