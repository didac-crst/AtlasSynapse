"""Provider-neutral LLM call logging interface.

Persists durable call telemetry in ``llm_call_log``, separate from ``operation_log``.
Raw prompts/responses are not stored by default; metadata is redacted via the
existing payload-retention policy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.models.enums import LlmCallStatus, LlmCostStatus
from semantic_memory.models.llm_calls import LlmCallLog
from semantic_memory.repositories.llm_calls import LlmCallLogRepository
from semantic_memory.services.llm_pricing import CostEstimate, estimate_cost
from semantic_memory.services.redaction import prepare_audit_payload


@dataclass
class LlmCallContext:
    """Correlation context for an LLM call."""

    purpose: str
    provider: str
    model: str
    actor_id: uuid.UUID | None = None
    operation_log_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    trace_id: uuid.UUID | None = None
    model_version: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LlmCallCompletion:
    status: LlmCallStatus
    outcome: str | None = None
    reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    provider_reported_cost: Decimal | None = None
    provider_reported_currency: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    completed_at: datetime | None = None


@runtime_checkable
class LlmCallLogger(Protocol):
    def begin(self, context: LlmCallContext) -> LlmCallLog: ...

    def complete(self, row: LlmCallLog, completion: LlmCallCompletion) -> LlmCallLog: ...


class NoOpLlmCallLogger:
    """Logger that records nothing (used when persistence is unavailable)."""

    def begin(self, context: LlmCallContext) -> LlmCallLog:
        return LlmCallLog(
            id=uuid.uuid4(),
            provider=context.provider,
            model=context.model,
            purpose=context.purpose,
            status=LlmCallStatus.STARTED.value,
            cost_status=LlmCostStatus.UNKNOWN.value,
            started_at=datetime.now(UTC),
            metadata_json={},
        )

    def complete(self, row: LlmCallLog, completion: LlmCallCompletion) -> LlmCallLog:
        row.status = completion.status.value
        row.outcome = completion.outcome
        return row


class DatabaseLlmCallLogger:
    """Append-only database-backed LLM call logger."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._repo = LlmCallLogRepository(session)

    def begin(self, context: LlmCallContext) -> LlmCallLog:
        metadata = prepare_audit_payload(
            context.metadata, mode=self._settings.raw_payload_retention
        )
        return self._repo.start(
            provider=context.provider,
            model=context.model,
            purpose=context.purpose,
            actor_id=context.actor_id,
            operation_log_id=context.operation_log_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
            model_version=context.model_version,
            reason=context.reason,
            metadata=metadata or {},
        )

    def complete(self, row: LlmCallLog, completion: LlmCallCompletion) -> LlmCallLog:
        cost = self._resolve_cost(row=row, completion=completion)
        metadata = prepare_audit_payload(
            completion.metadata, mode=self._settings.raw_payload_retention
        )
        return self._repo.finish(
            row,
            status=completion.status,
            outcome=completion.outcome,
            reason=completion.reason,
            completed_at=completion.completed_at,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            total_tokens=completion.total_tokens,
            cached_input_tokens=completion.cached_input_tokens,
            cost_amount=cost.amount,
            cost_currency=cost.currency,
            cost_status=cost.status,
            pricing_version=cost.pricing_version,
            pricing_snapshot=cost.pricing_snapshot,
            error_code=completion.error_code,
            error_message=completion.error_message,
            metadata=metadata if metadata is not None else row.metadata_json,
        )

    def _resolve_cost(self, *, row: LlmCallLog, completion: LlmCallCompletion) -> CostEstimate:
        return estimate_cost(
            provider=row.provider,
            model=row.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            provider_reported_cost=completion.provider_reported_cost,
            provider_reported_currency=completion.provider_reported_currency,
        )
