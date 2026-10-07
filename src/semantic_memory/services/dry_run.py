"""Annotate mutation responses after a dry-run savepoint rollback."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from semantic_memory.schemas.dry_run import (
    EntityWriteAction,
    OperationMode,
    StatementWriteAction,
)


def infer_would_persist(result: BaseModel) -> bool:
    """True when the operation would have written knowledge if dry_run were false."""
    data = result.model_dump(mode="python")
    outcome = data.get("outcome")
    if outcome is not None:
        value = str(outcome)
        if value in {"CLARIFY", "AMBIGUOUS"}:
            return False
        return True
    status = data.get("status")
    if status is not None:
        value = str(status).lower()
        if value in {"failed", "rejected"}:
            return False
        if value == "succeeded":
            return True
    return True


def infer_statement_action(result: BaseModel) -> StatementWriteAction:
    outcome = str(result.model_dump(mode="python").get("outcome"))
    if outcome == "CLARIFY":
        return StatementWriteAction.NOT_WRITTEN
    if outcome == "REUSE":
        return StatementWriteAction.WOULD_REUSE
    if outcome == "CREATE":
        return StatementWriteAction.WOULD_CREATE
    if outcome == "SUPERSEDE":
        return StatementWriteAction.WOULD_SUPERSEDE
    if outcome == "RETRACT":
        return StatementWriteAction.WOULD_RETRACT
    return StatementWriteAction.NOT_WRITTEN


def infer_entity_action(result: BaseModel) -> EntityWriteAction:
    outcome = str(result.model_dump(mode="python").get("outcome"))
    if outcome == "AMBIGUOUS":
        return EntityWriteAction.WOULD_CLARIFY
    if outcome == "REUSE":
        return EntityWriteAction.WOULD_REUSE
    if outcome == "CREATE":
        return EntityWriteAction.WOULD_CREATE
    return EntityWriteAction.NOT_WRITTEN


def annotate_dry_run_result[T: BaseModel](result: T) -> T:
    """Stamp dry-run observability fields onto a response model when present."""
    fields = result.__class__.model_fields
    updates: dict[str, Any] = {}
    if "dry_run" in fields:
        updates["dry_run"] = True
    if "operation_mode" in fields:
        updates["operation_mode"] = OperationMode.DRY_RUN
    if "would_persist" in fields:
        updates["would_persist"] = infer_would_persist(result)
    if "statement_action" in fields:
        updates["statement_action"] = infer_statement_action(result)
    if "entity_action" in fields:
        updates["entity_action"] = infer_entity_action(result)
    if not updates:
        return result
    return result.model_copy(update=updates)
