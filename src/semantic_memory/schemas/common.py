"""Shared request envelope schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class MutationEnvelope(BaseModel):
    """Required context for public mutating operations."""

    actor_key: str = Field(min_length=1)
    request_id: uuid.UUID
    idempotency_key: str = Field(min_length=1)
    trace_id: uuid.UUID | None = None
