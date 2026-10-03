"""Ingestion batch persistence."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models import BatchStatus, IngestionBatch


class BatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
