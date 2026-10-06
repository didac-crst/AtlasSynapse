"""Statement persistence and semantic-identity lookups."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from semantic_memory.models import Statement, StatementStatus
from semantic_memory.validation.literals import normalize_optional_to_utc


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

    def list_for_entity_timeline(self, entity_id: uuid.UUID | list[uuid.UUID]) -> list[Statement]:
        """Return statements involving an entity (or identity group), ordered by time."""
        entity_ids = [entity_id] if isinstance(entity_id, uuid.UUID) else list(entity_id)
        if not entity_ids:
            return []
        sort_time = func.coalesce(Statement.valid_from, Statement.asserted_at)
        return list(
            self._session.scalars(
                select(Statement)
                .where(
                    or_(
                        Statement.subject_entity_id.in_(entity_ids),
                        Statement.object_entity_id.in_(entity_ids),
                    )
                )
                .order_by(sort_time.asc(), Statement.asserted_at.asc(), Statement.created_at.asc())
            ).all()
        )

    def mark_superseded(self, statement: Statement, *, replacement_id: uuid.UUID) -> Statement:
        statement.status = StatementStatus.SUPERSEDED.value
        statement.superseded_by_statement_id = replacement_id
        self._session.flush()
        return statement

    def mark_retracted(self, statement: Statement) -> Statement:
        statement.status = StatementStatus.RETRACTED.value
        self._session.flush()
        return statement

    def reassign_entity_references(
        self,
        *,
        source_entity_id: uuid.UUID,
        target_entity_id: uuid.UUID,
    ) -> dict[str, int]:
        """Point statement endpoints at the merge survivor; retract exact duplicates."""
        moved_subject = 0
        moved_object = 0
        retracted_duplicates = 0

        subject_rows = list(
            self._session.scalars(
                select(Statement).where(Statement.subject_entity_id == source_entity_id)
            ).all()
        )
        for statement in subject_rows:
            # Use normalized_object so JSON (and all value kinds) compare correctly.
            duplicate = self._session.scalar(
                select(Statement.id).where(
                    Statement.id != statement.id,
                    Statement.subject_entity_id == target_entity_id,
                    Statement.predicate_id == statement.predicate_id,
                    Statement.normalized_object == statement.normalized_object,
                    Statement.valid_from.is_not_distinct_from(statement.valid_from),
                    Statement.valid_to.is_not_distinct_from(statement.valid_to),
                    Statement.status == StatementStatus.ASSERTED.value,
                )
            )
            if duplicate is not None and statement.status == StatementStatus.ASSERTED.value:
                statement.status = StatementStatus.RETRACTED.value
                retracted_duplicates += 1
                continue
            statement.subject_entity_id = target_entity_id
            moved_subject += 1

        object_rows = list(
            self._session.scalars(
                select(Statement).where(Statement.object_entity_id == source_entity_id)
            ).all()
        )
        for statement in object_rows:
            duplicate = self._session.scalar(
                select(Statement.id).where(
                    Statement.id != statement.id,
                    Statement.object_entity_id == target_entity_id,
                    Statement.subject_entity_id == statement.subject_entity_id,
                    Statement.predicate_id == statement.predicate_id,
                    Statement.valid_from.is_not_distinct_from(statement.valid_from),
                    Statement.valid_to.is_not_distinct_from(statement.valid_to),
                    Statement.status == StatementStatus.ASSERTED.value,
                )
            )
            if duplicate is not None and statement.status == StatementStatus.ASSERTED.value:
                statement.status = StatementStatus.RETRACTED.value
                retracted_duplicates += 1
                continue
            statement.object_entity_id = target_entity_id
            statement.normalized_object = f"entity:{target_entity_id}"
            moved_object += 1

        self._session.flush()
        return {
            "moved_subject": moved_subject,
            "moved_object": moved_object,
            "retracted_duplicates": retracted_duplicates,
        }

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

    def acquire_predicate_lock(
        self,
        *,
        subject_entity_id: uuid.UUID,
        predicate_id: uuid.UUID,
    ) -> None:
        """Serialize all assertions for a subject+predicate pair (cardinality-one)."""
        material = f"predicate:{subject_entity_id}:{predicate_id}".encode()
        self._advisory_lock(material)

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
        bound_from = normalize_optional_to_utc(valid_from)
        bound_to = normalize_optional_to_utc(valid_to)
        material = (
            f"assertion:{subject_entity_id}:{predicate_id}:{normalized_object}:"
            f"{bound_from.isoformat() if bound_from else ''}:"
            f"{bound_to.isoformat() if bound_to else ''}"
        ).encode()
        self._advisory_lock(material)

    def _advisory_lock(self, material: bytes) -> None:
        digest = hashlib.sha256(material).digest()[:8]
        lock_key = int.from_bytes(digest, byteorder="big", signed=False) % (2**63)
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
