"""Persistence for append-only LLM call logs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models.enums import LlmCallStatus, LlmCostStatus
from semantic_memory.models.llm_calls import LlmCallLog


class LlmCallLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, call_id: uuid.UUID) -> LlmCallLog | None:
        return self._session.get(LlmCallLog, call_id)

    def list_for_request(self, request_id: uuid.UUID) -> list[LlmCallLog]:
        return list(
            self._session.scalars(
                select(LlmCallLog)
                .where(LlmCallLog.request_id == request_id)
                .order_by(LlmCallLog.started_at.asc(), LlmCallLog.created_at.asc())
            ).all()
        )

    def start(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        actor_id: uuid.UUID | None = None,
        operation_log_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        model_version: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> LlmCallLog:
        row = LlmCallLog(
            id=uuid.uuid4(),
            actor_id=actor_id,
            operation_log_id=operation_log_id,
            request_id=request_id,
            trace_id=trace_id,
            provider=provider,
            model=model,
            model_version=model_version,
            purpose=purpose,
            reason=reason,
            status=LlmCallStatus.STARTED.value,
            cost_status=LlmCostStatus.UNKNOWN.value,
            started_at=started_at or datetime.now(UTC),
            metadata_json=metadata or {},
        )
        self._session.add(row)
        self._session.flush()
        return row

    def finish(
        self,
        row: LlmCallLog,
        *,
        status: LlmCallStatus | str,
        outcome: str | None = None,
        reason: str | None = None,
        completed_at: datetime | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        cost_amount: Decimal | None = None,
        cost_currency: str | None = None,
        cost_status: LlmCostStatus | str = LlmCostStatus.UNKNOWN,
        pricing_version: str | None = None,
        pricing_snapshot: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LlmCallLog:
        finished = completed_at or datetime.now(UTC)
        row.status = str(status)
        row.outcome = outcome
        if reason is not None:
            row.reason = reason
        row.completed_at = finished
        row.duration_ms = int((finished - row.started_at).total_seconds() * 1000)
        row.input_tokens = input_tokens
        row.output_tokens = output_tokens
        if total_tokens is not None:
            row.total_tokens = total_tokens
        elif input_tokens is not None or output_tokens is not None:
            row.total_tokens = (input_tokens or 0) + (output_tokens or 0)
        row.cached_input_tokens = cached_input_tokens
        row.cost_amount = cost_amount
        row.cost_currency = cost_currency
        row.cost_status = str(cost_status)
        row.pricing_version = pricing_version
        row.pricing_snapshot = pricing_snapshot
        row.error_code = error_code
        row.error_message = error_message
        if metadata is not None:
            row.metadata_json = metadata
        self._session.flush()
        return row
