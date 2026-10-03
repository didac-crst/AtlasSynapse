"""Conflict persistence and lookup."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from semantic_memory.models import Conflict, ConflictStatus, Statement


class ConflictRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, conflict_id: uuid.UUID) -> Conflict | None:
        return self._session.get(Conflict, conflict_id)

    def find_open_pair(
        self, *, statement_a_id: uuid.UUID, statement_b_id: uuid.UUID
    ) -> Conflict | None:
        left, right = _ordered_pair(statement_a_id, statement_b_id)
        return self._session.scalar(
            select(Conflict).where(
                Conflict.statement_a_id == left,
                Conflict.statement_b_id == right,
                Conflict.status == ConflictStatus.OPEN.value,
            )
        )

    def create(
        self,
        *,
        statement_a_id: uuid.UUID,
        statement_b_id: uuid.UUID,
        conflict_type: str,
        details: dict[str, Any] | None = None,
    ) -> Conflict:
        left, right = _ordered_pair(statement_a_id, statement_b_id)
        row = Conflict(
            id=uuid.uuid4(),
            statement_a_id=left,
            statement_b_id=right,
            status=ConflictStatus.OPEN.value,
            conflict_type=conflict_type,
            details=details or {},
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_conflicts(
        self,
        *,
        entity_id: uuid.UUID | None = None,
        statement_id: uuid.UUID | None = None,
        status: ConflictStatus | str | None = ConflictStatus.OPEN,
    ) -> list[Conflict]:
        stmt = select(Conflict)
        if status is not None:
            stmt = stmt.where(Conflict.status == str(status))
        if statement_id is not None:
            stmt = stmt.where(
                or_(
                    Conflict.statement_a_id == statement_id,
                    Conflict.statement_b_id == statement_id,
                )
            )
        if entity_id is not None:
            stmt = (
                stmt.join(
                    Statement,
                    or_(
                        Statement.id == Conflict.statement_a_id,
                        Statement.id == Conflict.statement_b_id,
                    ),
                )
                .where(
                    or_(
                        Statement.subject_entity_id == entity_id,
                        Statement.object_entity_id == entity_id,
                    )
                )
                .distinct()
            )
        stmt = stmt.order_by(Conflict.created_at.asc(), Conflict.id.asc())
        return list(self._session.scalars(stmt).all())

    def set_status(
        self,
        conflict: Conflict,
        *,
        status: ConflictStatus,
        resolved_by_actor_id: uuid.UUID | None,
    ) -> Conflict:
        conflict.status = status.value
        conflict.resolved_by_actor_id = resolved_by_actor_id
        self._session.flush()
        return conflict


def _ordered_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a.int <= b.int else (b, a)
