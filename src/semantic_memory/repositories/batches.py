"""Ingestion batch persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.models import BatchStatus, IngestionBatch


class BatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, batch_id: uuid.UUID) -> IngestionBatch | None:
        return self._session.get(IngestionBatch, batch_id)

    def create(
        self,
        *,
        actor_id: uuid.UUID,
        request_id: uuid.UUID,
        item_count: int,
        metadata_json: dict[str, Any] | None = None,
        status: BatchStatus = BatchStatus.RUNNING,
    ) -> IngestionBatch:
        row = IngestionBatch(
            id=uuid.uuid4(),
            actor_id=actor_id,
            status=status.value,
            item_count=item_count,
            request_id=request_id,
            metadata_json=metadata_json or {},
        )
        self._session.add(row)
        self._session.flush()
        return row

    def set_status(
        self,
        batch: IngestionBatch,
        *,
        status: BatchStatus,
        metadata_json: dict[str, Any] | None = None,
    ) -> IngestionBatch:
        batch.status = status.value
        if metadata_json is not None:
            batch.metadata_json = metadata_json
        self._session.flush()
        return batch

    def list(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[IngestionBatch]:
        stmt = self._filtered_select(
            status=status,
            actor_id=actor_id,
            request_id=request_id,
            created_after=created_after,
            created_before=created_before,
        ).order_by(IngestionBatch.created_at.desc(), IngestionBatch.id.desc())
        return list(self._session.scalars(stmt.limit(limit).offset(offset)).all())

    def count(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        filtered = self._filtered_select(
            status=status,
            actor_id=actor_id,
            request_id=request_id,
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
                select(IngestionBatch.status, func.count()),
                created_after=created_after,
                created_before=created_before,
            ).group_by(IngestionBatch.status)
        )
        item_rows = self._session.execute(
            self._time_filters(
                select(
                    IngestionBatch.status,
                    func.coalesce(func.sum(IngestionBatch.item_count), 0),
                ),
                created_after=created_after,
                created_before=created_before,
            ).group_by(IngestionBatch.status)
        )
        return {
            "by_status": {str(k): int(v) for k, v in by_status_rows},
            "item_count_by_status": {str(k): int(v) for k, v in item_rows},
        }

    def _filtered_select(
        self,
        *,
        status: str | None,
        actor_id: uuid.UUID | None,
        request_id: uuid.UUID | None,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        stmt = select(IngestionBatch)
        if status is not None:
            stmt = stmt.where(IngestionBatch.status == status)
        if actor_id is not None:
            stmt = stmt.where(IngestionBatch.actor_id == actor_id)
        if request_id is not None:
            stmt = stmt.where(IngestionBatch.request_id == request_id)
        return self._time_filters(stmt, created_after=created_after, created_before=created_before)

    @staticmethod
    def _time_filters(
        stmt: Any,
        *,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        if created_after is not None:
            stmt = stmt.where(IngestionBatch.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(IngestionBatch.created_at <= created_before)
        return stmt
