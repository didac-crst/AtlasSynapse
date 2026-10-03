"""Operation-log persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models import OperationLog
from semantic_memory.models.enums import OperationStatus


class OperationLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
