"""Dry-run mode enums shared by mutation requests and responses."""

from __future__ import annotations

from enum import StrEnum


class OperationMode(StrEnum):
    """Whether a mutation actually executes or only previews."""

    EXECUTE = "execute"
    DRY_RUN = "dry_run"


class StatementWriteAction(StrEnum):
    """What a statement mutation would do (or did) to the statement graph."""

    CREATE = "CREATE"
    REUSE = "REUSE"
    SUPERSEDE = "SUPERSEDE"
    RETRACT = "RETRACT"
    NOT_WRITTEN = "NOT_WRITTEN"
    WOULD_CREATE = "WOULD_CREATE"
    WOULD_REUSE = "WOULD_REUSE"
    WOULD_SUPERSEDE = "WOULD_SUPERSEDE"
    WOULD_RETRACT = "WOULD_RETRACT"


class EntityWriteAction(StrEnum):
    """What create_entity would do (or did)."""

    CREATE = "CREATE"
    REUSE = "REUSE"
    NOT_WRITTEN = "NOT_WRITTEN"
    WOULD_CREATE = "WOULD_CREATE"
    WOULD_REUSE = "WOULD_REUSE"
    WOULD_CLARIFY = "WOULD_CLARIFY"
