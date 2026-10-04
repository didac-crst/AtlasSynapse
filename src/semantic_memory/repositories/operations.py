"""Operation-log persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.models import OperationLog
from semantic_memory.models.enums import OperationStatus


class OperationLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, operation_id: uuid.UUID) -> OperationLog | None:
        return self._session.get(OperationLog, operation_id)

    def start(
        self,
        *,
        actor_id: uuid.UUID,
        operation_name: str,
        request_id: uuid.UUID,
        trace_id: uuid.UUID | None,
        idempotency_key: str | None,
        request_payload: dict[str, Any] | None,
    ) -> OperationLog:
        row = OperationLog(
            id=uuid.uuid4(),
            actor_id=actor_id,
            operation_name=operation_name,
            request_id=request_id,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            status=OperationStatus.STARTED.value,
            request_payload=request_payload,
            started_at=datetime.now(UTC),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def finish(
        self,
        row: OperationLog,
        *,
        status: OperationStatus | str,
        response_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> OperationLog:
        row.status = str(status)
        row.response_payload = response_payload
        row.error_code = error_code
        row.error_message = error_message
        row.finished_at = datetime.now(UTC)
        self._session.flush()
        return row

    def list(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        operation_name: str | None = None,
        error_code: str | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OperationLog]:
        stmt = self._filtered_select(
            status=status,
            actor_id=actor_id,
            operation_name=operation_name,
            error_code=error_code,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        ).order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        stmt = stmt.limit(limit).offset(offset)
        return list(self._session.scalars(stmt).all())

    def count(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        operation_name: str | None = None,
        error_code: str | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(
            self._filtered_select(
                status=status,
                actor_id=actor_id,
                operation_name=operation_name,
                error_code=error_code,
                request_id=request_id,
                trace_id=trace_id,
                created_after=created_after,
                created_before=created_before,
            ).subquery()
        )
        return int(self._session.scalar(stmt) or 0)

    def aggregate_summary(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        base = self._time_filters(
            select(OperationLog.status, func.count()),
            created_after=created_after,
            created_before=created_before,
        ).group_by(OperationLog.status)
        by_status = {str(status): int(count) for status, count in self._session.execute(base)}

        failed = self._time_filters(
            select(OperationLog.operation_name, func.count()).where(
                OperationLog.status == OperationStatus.FAILED.value
            ),
            created_after=created_after,
            created_before=created_before,
        ).group_by(OperationLog.operation_name)
        failed_by_operation = {
            str(name): int(count) for name, count in self._session.execute(failed)
        }

        rejected = self._time_filters(
            select(OperationLog.error_code, func.count()).where(
                OperationLog.status == OperationStatus.REJECTED.value,
                OperationLog.error_code.is_not(None),
            ),
            created_after=created_after,
            created_before=created_before,
        ).group_by(OperationLog.error_code)
        rejected_by_error_code = {
            str(code): int(count) for code, count in self._session.execute(rejected)
        }

        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "failed_by_operation": failed_by_operation,
            "rejected_by_error_code": rejected_by_error_code,
        }

    def _filtered_select(
        self,
        *,
        status: str | None,
        actor_id: uuid.UUID | None,
        operation_name: str | None,
        error_code: str | None,
        request_id: uuid.UUID | None,
        trace_id: uuid.UUID | None,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        stmt = select(OperationLog)
        if status is not None:
            stmt = stmt.where(OperationLog.status == status)
        if actor_id is not None:
            stmt = stmt.where(OperationLog.actor_id == actor_id)
        if operation_name is not None:
            stmt = stmt.where(OperationLog.operation_name == operation_name)
        if error_code is not None:
            stmt = stmt.where(OperationLog.error_code == error_code)
        if request_id is not None:
            stmt = stmt.where(OperationLog.request_id == request_id)
        if trace_id is not None:
            stmt = stmt.where(OperationLog.trace_id == trace_id)
        return self._time_filters(stmt, created_after=created_after, created_before=created_before)

    @staticmethod
    def _time_filters(
        stmt: Any,
        *,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        if created_after is not None:
            stmt = stmt.where(OperationLog.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(OperationLog.created_at <= created_before)
        return stmt
