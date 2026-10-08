"""memory quality structural repair resolution

Revision ID: c2d3e4f5a6b7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-08 10:55:00.000000

Allow resolution=structural_repair for governed supersession edge repairs.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    op.drop_constraint(
        op.f("ck_memory_quality_issue_resolution"),
        "memory_quality_issue",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_memory_quality_issue_resolution"),
        "memory_quality_issue",
        "resolution IS NULL OR resolution IN (" + ", ".join(repr(v) for v in _RESOLUTIONS) + ")",
    )


def downgrade() -> None:
    old = (
        "auto_resolved",
        "corrected",
        "retracted",
        "superseded",
        "confirmed_same",
        "confirmed_different",
        "unknown",
        "no_action",
    )
    op.drop_constraint(
        op.f("ck_memory_quality_issue_resolution"),
        "memory_quality_issue",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_memory_quality_issue_resolution"),
        "memory_quality_issue",
        "resolution IS NULL OR resolution IN (" + ", ".join(repr(v) for v in old) + ")",
    )
