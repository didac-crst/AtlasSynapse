"""alias identity strength

Revision ID: e2a7c1d84b3f
Revises: d1f4a8c90b2e
Create Date: 2026-10-06 18:15:00.000000

Persist mandatory entity-alias identity_strength. Existing rows backfill to
supporting so legacy aliases are never treated as authoritative by accident.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a7c1d84b3f"
down_revision: str | Sequence[str] | None = "d1f4a8c90b2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entity_alias",
        sa.Column(
            "identity_strength",
            sa.String(length=32),
            nullable=False,
            server_default="supporting",
        ),
    )
    # Explicit backfill (server_default covers new rows; make intent obvious).
    op.execute(
        sa.text(
            "UPDATE entity_alias SET identity_strength = 'supporting' "
            "WHERE identity_strength IS NULL OR identity_strength = ''"
        )
    )
    op.create_check_constraint(
        "ck_entity_alias_identity_strength",
        "entity_alias",
        "identity_strength IN ('supporting', 'authoritative')",
    )
    op.create_index(
        "ix_entity_alias_identity_strength",
        "entity_alias",
        ["identity_strength"],
    )
    # Keep NOT NULL without relying on server_default forever for application inserts.
    op.alter_column(
        "entity_alias",
        "identity_strength",
        server_default=None,
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_index("ix_entity_alias_identity_strength", table_name="entity_alias")
    op.drop_constraint(
        "ck_entity_alias_identity_strength",
        "entity_alias",
        type_="check",
    )
    op.drop_column("entity_alias", "identity_strength")
