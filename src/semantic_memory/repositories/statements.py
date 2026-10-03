"""Statement persistence and semantic-identity lookups."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from semantic_memory.models import Statement, StatementStatus


class StatementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, statement_id: uuid.UUID) -> Statement | None:
        return self._session.get(Statement, statement_id)

    def find_semantic_duplicate(
        self,
        *,
        subject_entity_id: uuid.UUID,
        predicate_id: uuid.UUID,
        normalized_object: str,
        valid_from: datetime | None,
        valid_to: datetime | None,
    ) -> Statement | None:
        stmt = select(Statement).where(
            Statement.subject_entity_id == subject_entity_id,
            Statement.predicate_id == predicate_id,
            Statement.normalized_object == normalized_object,
            Statement.status == StatementStatus.ASSERTED.value,
        )
        if valid_from is None:
            stmt = stmt.where(Statement.valid_from.is_(None))
        else:
            stmt = stmt.where(Statement.valid_from == valid_from)
        if valid_to is None:
            stmt = stmt.where(Statement.valid_to.is_(None))
        else:
            stmt = stmt.where(Statement.valid_to == valid_to)
        return self._session.scalar(stmt)

    def find_asserted_for_predicate(
        self,
        *,
        subject_entity_id: uuid.UUID,
        predicate_id: uuid.UUID,
    ) -> list[Statement]:
        return list(
            self._session.scalars(
                select(Statement).where(
                    Statement.subject_entity_id == subject_entity_id,
                    Statement.predicate_id == predicate_id,
                    Statement.status == StatementStatus.ASSERTED.value,
                )
            ).all()
        )

    def create(
        self,
        *,
        subject_entity_id: uuid.UUID,
        predicate_id: uuid.UUID,
        actor_id: uuid.UUID,
        asserted_at: datetime,
        normalized_object: str,
        object_entity_id: uuid.UUID | None = None,
        object_string: str | None = None,
        object_number: Decimal | None = None,
        object_boolean: bool | None = None,
        object_datetime: datetime | None = None,
        object_json: dict[str, Any] | list[Any] | None = None,
        observed_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        confidence: Decimal | None = None,
        statement_id: uuid.UUID | None = None,
    ) -> Statement:
        # Omit object_json when unset so the driver stores SQL NULL, not JSON null.
        row = Statement(
            id=statement_id or uuid.uuid4(),
            subject_entity_id=subject_entity_id,
            predicate_id=predicate_id,
            object_entity_id=object_entity_id,
            object_string=object_string,
            object_number=object_number,
            object_boolean=object_boolean,
            object_datetime=object_datetime,
            status=StatementStatus.ASSERTED.value,
            asserted_at=asserted_at,
            observed_at=observed_at,
            valid_from=valid_from,
            valid_to=valid_to,
            confidence=confidence,
            actor_id=actor_id,
            normalized_object=normalized_object,
            metadata_json={},
        )
        if object_json is not None:
            row.object_json = object_json
        self._session.add(row)
        self._session.flush()
        return row

    def acquire_assertion_lock(
        self,
        *,
        subject_entity_id: uuid.UUID,
        predicate_id: uuid.UUID,
        normalized_object: str,
        valid_from: datetime | None,
        valid_to: datetime | None,
    ) -> None:
        """Serialize assertion for a semantic identity key."""
        material = (
            f"{subject_entity_id}:{predicate_id}:{normalized_object}:"
            f"{valid_from.isoformat() if valid_from else ''}:"
            f"{valid_to.isoformat() if valid_to else ''}"
        ).encode()
        digest = hashlib.sha256(material).digest()[:8]
        lock_key = int.from_bytes(digest, byteorder="big", signed=False) % (2**63)
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
