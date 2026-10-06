"""allow repeated content hashes per source

Revision ID: a9c4e1f72b8d
Revises: f3b8e2a91c4d
Create Date: 2026-10-06 19:20:00.000000

Drop the unique (source_id, canonical_content_hash) constraint so reverting to a
prior body (A → B → A) creates a new latest revision instead of REUSE-ing the
stale older row. Keep a non-unique index for hash lookups.

Downgrade keeps the highest revision_number per (source_id, hash) and deletes
older duplicates so the unique constraint can be restored.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a9c4e1f72b8d"
down_revision: str | Sequence[str] | None = "f3b8e2a91c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_source_content_revision_source_canonical_hash",
        "source_content_revision",
        type_="unique",
    )
    op.create_index(
        "ix_source_content_revision_source_canonical_hash",
        "source_content_revision",
        ["source_id", "canonical_content_hash"],
        unique=False,
    )


def downgrade() -> None:
    # Keep the newest revision for each (source_id, hash); drop earlier repeats
    # so recreating the unique constraint cannot fail after A→B→A history.
    op.execute(
        """
        DELETE FROM source_content_revision AS older
        USING source_content_revision AS newer
        WHERE older.source_id = newer.source_id
          AND older.canonical_content_hash = newer.canonical_content_hash
          AND older.revision_number < newer.revision_number
        """
    )
    op.drop_index(
        "ix_source_content_revision_source_canonical_hash",
        table_name="source_content_revision",
    )
    op.create_unique_constraint(
        "uq_source_content_revision_source_canonical_hash",
        "source_content_revision",
        ["source_id", "canonical_content_hash"],
    )
