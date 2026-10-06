"""Dry-run mode enums shared by mutation requests and responses."""

from __future__ import annotations

from enum import StrEnum


class OperationMode(StrEnum):
    """Whether a mutation actually executes or only previews."""

    EXECUTE = "execute"
    DRY_RUN = "dry_run"


class StatementWriteAction(StrEnum):
    """What assert would do (or did) to the statement graph."""

    CREATE = "CREATE"
    REUSE = "REUSE"
    NOT_WRITTEN = "NOT_WRITTEN"
    WOULD_CREATE = "WOULD_CREATE"
    WOULD_REUSE = "WOULD_REUSE"


class EntityWriteAction(StrEnum):
    """What create_entity would do (or did)."""

    CREATE = "CREATE"
    REUSE = "REUSE"
    NOT_WRITTEN = "NOT_WRITTEN"
    WOULD_CREATE = "WOULD_CREATE"
    WOULD_REUSE = "WOULD_REUSE"
    WOULD_CLARIFY = "WOULD_CLARIFY"
