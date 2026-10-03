"""Idempotency reservation persistence."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from semantic_memory.models import IdempotencyRecord


def hash_request_payload(payload: dict[str, Any]) -> str:
    """Return a stable hash for an idempotent request payload."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, actor_id: uuid.UUID, idempotency_key: str) -> IdempotencyRecord | None:
        return self._session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )

    def reserve_or_get(
        self,
        *,
        actor_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
        operation_name: str,
    ) -> tuple[IdempotencyRecord, bool]:
        """Atomically reserve an idempotency key or return the existing row.

        Returns ``(record, created)`` where ``created`` is True when this caller
        inserted the reservation.
        """
        stmt = (
            insert(IdempotencyRecord)
            .values(
                id=uuid.uuid4(),
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation_name=operation_name,
                response_payload=None,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_record_actor_key")
            .returning(IdempotencyRecord.id)
        )
        inserted_id = self._session.execute(stmt).scalar_one_or_none()
        self._session.flush()
        record = self.get(actor_id=actor_id, idempotency_key=idempotency_key)
        if record is None:
            raise RuntimeError("Failed to reserve or load idempotency record")
        return record, inserted_id is not None

    def store_response(
        self, record: IdempotencyRecord, response_payload: dict[str, Any]
    ) -> IdempotencyRecord:
        record.response_payload = response_payload
        self._session.flush()
        return record
