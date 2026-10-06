"""Shared request envelope schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from semantic_memory.schemas.dry_run import OperationMode


class MutationEnvelope(BaseModel):
    """Required context for public mutating operations."""

    actor_key: str = Field(min_length=1)
    request_id: uuid.UUID
    idempotency_key: str = Field(min_length=1)
    trace_id: uuid.UUID | None = None
    dry_run: bool = Field(
        default=False,
        description=(
            "When true, run the full mutation decision path (identity, validation, "
            "review) and return what would happen, but persist no knowledge writes. "
            "Operational logs are still recorded with operation_mode=dry_run."
        ),
    )


class DryRunResponseFields(BaseModel):
    """Common dry-run fields for mutation responses."""

    dry_run: bool = False
    operation_mode: OperationMode = OperationMode.EXECUTE
    would_persist: bool | None = None
