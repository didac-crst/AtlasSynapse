"""Admin inspection request/response schemas (metadata-first)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class AdminListMeta(BaseModel):
    total: int
    limit: int
    offset: int


class OperationSummary(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    operation_name: str
    status: str
    error_code: str | None = None
    error_message: str | None = None
    request_id: uuid.UUID
    trace_id: uuid.UUID | None = None
    idempotency_key: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    created_at: datetime


class OperationDetail(OperationSummary):
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None


class OperationListResponse(AdminListMeta):
    items: list[OperationSummary] = Field(default_factory=list)


class LlmCallSummary(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None = None
    operation_log_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    trace_id: uuid.UUID | None = None
    provider: str
    model: str
    purpose: str
    status: str
    outcome: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_amount: Decimal | None = None
    cost_currency: str | None = None
    cost_status: str
    error_code: str | None = None
    created_at: datetime


class LlmCallDetail(LlmCallSummary):
    model_version: str | None = None
    reason: str | None = None
    cached_input_tokens: int | None = None
    pricing_version: str | None = None
    pricing_snapshot: dict[str, Any] | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None


class LlmCallListResponse(AdminListMeta):
    items: list[LlmCallSummary] = Field(default_factory=list)


class FeedbackSummary(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    feedback_type: str
    severity: str
    title: str
    status: str
    request_id: uuid.UUID | None = None
    trace_id: uuid.UUID | None = None
    operation_log_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    statement_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    fingerprint: str | None = None
    occurrence_count: int
    last_seen_at: datetime
    created_at: datetime


class FeedbackDetail(FeedbackSummary):
    description: str
    resolution: str | None = None
    resolved_by_actor_id: uuid.UUID | None = None
    resolved_at: datetime | None = None
    context: dict[str, Any] | None = None


class FeedbackListResponse(AdminListMeta):
    items: list[FeedbackSummary] = Field(default_factory=list)


class ProposalSummary(BaseModel):
    id: uuid.UUID
    proposed_by_actor_id: uuid.UUID
    status: str
    proposal_type: str
    summary: str
    base_revision_number: int | None = None
    request_id: uuid.UUID
    decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ProposalDetail(ProposalSummary):
    payload: dict[str, Any] | None = None


class ProposalListResponse(AdminListMeta):
    items: list[ProposalSummary] = Field(default_factory=list)


class ConflictSummary(BaseModel):
    id: uuid.UUID
    statement_a_id: uuid.UUID
    statement_b_id: uuid.UUID
    status: str
    conflict_type: str
    resolved_by_actor_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class ConflictDetail(ConflictSummary):
    details: dict[str, Any] = Field(default_factory=dict)


class ConflictListResponse(AdminListMeta):
    items: list[ConflictSummary] = Field(default_factory=list)


class BatchSummary(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    status: str
    item_count: int
    request_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class BatchDetail(BatchSummary):
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatchListResponse(AdminListMeta):
    items: list[BatchSummary] = Field(default_factory=list)


class OperationsSummaryBlock(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    failed_by_operation: dict[str, int] = Field(default_factory=dict)
    rejected_by_error_code: dict[str, int] = Field(default_factory=dict)


class LlmCallsSummaryBlock(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_purpose: dict[str, int] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    known_cost_calls: int = 0
    unknown_cost_calls: int = 0
    cost_by_currency: dict[str, str] = Field(default_factory=dict)


class FeedbackSummaryBlock(BaseModel):
    total_open: int
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    repeated_open_feedback: int = 0


class ProposalsSummaryBlock(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)


class ConflictsSummaryBlock(BaseModel):
    by_status: dict[str, int] = Field(default_factory=dict)


class BatchesSummaryBlock(BaseModel):
    by_status: dict[str, int] = Field(default_factory=dict)
    item_count_by_status: dict[str, int] = Field(default_factory=dict)


class AdminSummaryResponse(BaseModel):
    operations: OperationsSummaryBlock
    llm_calls: LlmCallsSummaryBlock
    feedback: FeedbackSummaryBlock
    proposals: ProposalsSummaryBlock
    conflicts: ConflictsSummaryBlock
    batches: BatchesSummaryBlock
