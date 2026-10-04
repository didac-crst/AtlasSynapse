"""Agent feedback request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from semantic_memory.models.enums import (
    FeedbackOutcome,
    FeedbackSeverity,
    FeedbackStatus,
    FeedbackType,
)
from semantic_memory.schemas.common import MutationEnvelope


class ReportFeedbackRequest(MutationEnvelope):
    feedback_type: FeedbackType
    severity: FeedbackSeverity
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=8000)
    operation_log_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    statement_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class FeedbackResponse(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    feedback_type: FeedbackType
    severity: FeedbackSeverity
    title: str
    description: str
    request_id: uuid.UUID | None = None
    trace_id: uuid.UUID | None = None
    operation_log_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    statement_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str | None = None
    status: FeedbackStatus
    resolution: str | None = None
    resolved_by_actor_id: uuid.UUID | None = None
    resolved_at: datetime | None = None
    occurrence_count: int
    last_seen_at: datetime
    created_at: datetime


class ReportFeedbackResponse(BaseModel):
    outcome: FeedbackOutcome
    feedback: FeedbackResponse
    request_id: uuid.UUID


class ListFeedbackResponse(BaseModel):
    items: list[FeedbackResponse] = Field(default_factory=list)


class ResolveFeedbackRequest(MutationEnvelope):
    feedback_id: uuid.UUID
    status: FeedbackStatus
    resolution: str | None = Field(default=None, max_length=8000)


class ResolveFeedbackResponse(BaseModel):
    feedback: FeedbackResponse
    request_id: uuid.UUID
