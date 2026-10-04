"""Persistence for LLM call logs (insert on begin, update on complete)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
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

    def list(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        purpose: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        outcome: str | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LlmCallLog]:
        stmt = self._filtered_select(
            status=status,
            actor_id=actor_id,
            purpose=purpose,
            provider=provider,
            model=model,
            outcome=outcome,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        ).order_by(LlmCallLog.created_at.desc(), LlmCallLog.id.desc())
        return list(self._session.scalars(stmt.limit(limit).offset(offset)).all())

    def count(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        purpose: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        outcome: str | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        filtered = self._filtered_select(
            status=status,
            actor_id=actor_id,
            purpose=purpose,
            provider=provider,
            model=model,
            outcome=outcome,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        ).subquery()
        return int(self._session.scalar(select(func.count()).select_from(filtered)) or 0)

    def aggregate_summary(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        by_status_rows = self._session.execute(
            self._time_filters(
                select(LlmCallLog.status, func.count()),
                created_after=created_after,
                created_before=created_before,
            ).group_by(LlmCallLog.status)
        )
        by_status = {str(status): int(count) for status, count in by_status_rows}

        by_purpose_rows = self._session.execute(
            self._time_filters(
                select(LlmCallLog.purpose, func.count()),
                created_after=created_after,
                created_before=created_before,
            ).group_by(LlmCallLog.purpose)
        )
        by_purpose = {str(purpose): int(count) for purpose, count in by_purpose_rows}

        token_row = self._session.execute(
            self._time_filters(
                select(
                    func.coalesce(func.sum(LlmCallLog.input_tokens), 0),
                    func.coalesce(func.sum(LlmCallLog.output_tokens), 0),
                    func.coalesce(func.sum(LlmCallLog.total_tokens), 0),
                ),
                created_after=created_after,
                created_before=created_before,
            )
        ).one()

        known_cost = int(
            self._session.scalar(
                self._time_filters(
                    select(func.count()).where(
                        LlmCallLog.cost_status != LlmCostStatus.UNKNOWN.value,
                        LlmCallLog.cost_amount.is_not(None),
                    ),
                    created_after=created_after,
                    created_before=created_before,
                )
            )
            or 0
        )
        total_calls = sum(by_status.values())
        unknown_cost = total_calls - known_cost

        cost_rows = self._session.execute(
            self._time_filters(
                select(
                    LlmCallLog.cost_currency,
                    func.coalesce(func.sum(LlmCallLog.cost_amount), 0),
                ).where(
                    LlmCallLog.cost_amount.is_not(None),
                    LlmCallLog.cost_currency.is_not(None),
                ),
                created_after=created_after,
                created_before=created_before,
            ).group_by(LlmCallLog.cost_currency)
        )
        cost_by_currency = {
            str(currency): str(amount) for currency, amount in cost_rows if currency is not None
        }

        return {
            "total": total_calls,
            "by_status": by_status,
            "by_purpose": by_purpose,
            "input_tokens": int(token_row[0] or 0),
            "output_tokens": int(token_row[1] or 0),
            "total_tokens": int(token_row[2] or 0),
            "known_cost_calls": known_cost,
            "unknown_cost_calls": unknown_cost,
            "cost_by_currency": cost_by_currency,
        }

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

    def _filtered_select(
        self,
        *,
        status: str | None,
        actor_id: uuid.UUID | None,
        purpose: str | None,
        provider: str | None,
        model: str | None,
        outcome: str | None,
        request_id: uuid.UUID | None,
        trace_id: uuid.UUID | None,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        stmt = select(LlmCallLog)
        if status is not None:
            stmt = stmt.where(LlmCallLog.status == status)
        if actor_id is not None:
            stmt = stmt.where(LlmCallLog.actor_id == actor_id)
        if purpose is not None:
            stmt = stmt.where(LlmCallLog.purpose == purpose)
        if provider is not None:
            stmt = stmt.where(LlmCallLog.provider == provider)
        if model is not None:
            stmt = stmt.where(LlmCallLog.model == model)
        if outcome is not None:
            stmt = stmt.where(LlmCallLog.outcome == outcome)
        if request_id is not None:
            stmt = stmt.where(LlmCallLog.request_id == request_id)
        if trace_id is not None:
            stmt = stmt.where(LlmCallLog.trace_id == trace_id)
        return self._time_filters(stmt, created_after=created_after, created_before=created_before)

    @staticmethod
    def _time_filters(
        stmt: Any,
        *,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        if created_after is not None:
            stmt = stmt.where(LlmCallLog.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(LlmCallLog.created_at <= created_before)
        return stmt
