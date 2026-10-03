"""Conflict detection support, retrieval, and explicit resolution metadata."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    InvalidStateTransitionError,
    UnknownConflictError,
    ValidationFailedError,
)
from semantic_memory.models import Conflict, ConflictStatus, Statement
from semantic_memory.models.capabilities import Capability
from semantic_memory.repositories.conflicts import ConflictRepository
from semantic_memory.schemas.conflicts import (
    ConflictMutationResponse,
    ConflictResponse,
    DismissConflictRequest,
    FindConflictsResponse,
    ResolveConflictRequest,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.mutations import MutationRunner


def intervals_overlap(
    a_from: datetime | None,
    a_to: datetime | None,
    b_from: datetime | None,
    b_to: datetime | None,
) -> bool:
    """Return True when two half-open-style validity windows overlap.

    Missing bounds are open-ended. Equal endpoints still overlap so adjacent
    closed intervals are treated conservatively as conflicting.
    """
    if a_from is not None and b_to is not None and a_from > b_to:
        return False
    if b_from is not None and a_to is not None and b_from > a_to:
        return False
    return True


class ConflictService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._conflicts = ConflictRepository(session)
        self._mutations = MutationRunner(session)

    def find_conflicts(
        self,
        *,
        entity_id: uuid.UUID | None = None,
        statement_id: uuid.UUID | None = None,
        status: ConflictStatus | str | None = ConflictStatus.OPEN,
    ) -> FindConflictsResponse:
        rows = self._conflicts.list_conflicts(
            entity_id=entity_id,
            statement_id=statement_id,
            status=status,
        )
        return FindConflictsResponse(conflicts=[self._to_response(row) for row in rows])

    def resolve_conflict(self, request: ResolveConflictRequest) -> ConflictMutationResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="resolve_conflict",
            request=request,
            response_model=ConflictMutationResponse,
            constraint_name="conflict_resolve",
            execute=lambda: self._set_status_body(
                conflict_id=request.conflict_id,
                status=ConflictStatus.RESOLVED,
                actor_id=actor.id,
                request_id=request.request_id,
                resolution_note=request.resolution_note,
            ),
        )

    def dismiss_conflict(self, request: DismissConflictRequest) -> ConflictMutationResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="dismiss_conflict",
            request=request,
            response_model=ConflictMutationResponse,
            constraint_name="conflict_dismiss",
            execute=lambda: self._set_status_body(
                conflict_id=request.conflict_id,
                status=ConflictStatus.DISMISSED,
                actor_id=actor.id,
                request_id=request.request_id,
                resolution_note=request.resolution_note,
            ),
        )

    def record_cardinality_conflicts(
        self,
        *,
        new_statement: Statement,
        existing: list[Statement],
        predicate_key: str,
    ) -> list[Conflict]:
        """Persist open conflicts for overlapping incompatible asserted values.

        Does not retract, supersede, or otherwise mutate the statements.
        """
        recorded: list[Conflict] = []
        for other in existing:
            if other.id == new_statement.id:
                continue
            if other.normalized_object == new_statement.normalized_object:
                continue
            if not intervals_overlap(
                new_statement.valid_from,
                new_statement.valid_to,
                other.valid_from,
                other.valid_to,
            ):
                continue
            existing_conflict = self._conflicts.find_open_pair(
                statement_a_id=new_statement.id,
                statement_b_id=other.id,
            )
            if existing_conflict is not None:
                recorded.append(existing_conflict)
                continue
            details: dict[str, Any] = {
                "predicate_key": predicate_key,
                "subject_entity_id": str(new_statement.subject_entity_id),
                "reason": "overlapping_cardinality_one",
            }
            recorded.append(
                self._conflicts.create(
                    statement_a_id=new_statement.id,
                    statement_b_id=other.id,
                    conflict_type="cardinality",
                    details=details,
                )
            )
        return recorded

    def _set_status_body(
        self,
        *,
        conflict_id: uuid.UUID,
        status: ConflictStatus,
        actor_id: uuid.UUID,
        request_id: uuid.UUID,
        resolution_note: str | None,
    ) -> ConflictMutationResponse:
        conflict = self._conflicts.get(conflict_id)
        if conflict is None:
            raise UnknownConflictError(
                f"Conflict {conflict_id} was not found",
                details={"conflict_id": str(conflict_id)},
                request_id=str(request_id),
            )
        if conflict.status != ConflictStatus.OPEN.value:
            raise InvalidStateTransitionError(
                f"Conflict {conflict_id} is not open",
                details={
                    "conflict_id": str(conflict_id),
                    "status": conflict.status,
                },
                request_id=str(request_id),
            )
        if resolution_note is not None and not resolution_note.strip():
            raise ValidationFailedError(
                "resolution_note must not be blank when provided",
                details={"conflict_id": str(conflict_id)},
                request_id=str(request_id),
            )
        details = dict(conflict.details or {})
        if resolution_note is not None:
            details["resolution_note"] = resolution_note.strip()
        conflict.details = details
        updated = self._conflicts.set_status(
            conflict,
            status=status,
            resolved_by_actor_id=actor_id,
        )
        return ConflictMutationResponse(
            conflict=self._to_response(updated),
            request_id=request_id,
        )

    def _to_response(self, conflict: Conflict) -> ConflictResponse:
        return ConflictResponse(
            id=conflict.id,
            statement_a_id=conflict.statement_a_id,
            statement_b_id=conflict.statement_b_id,
            status=ConflictStatus(conflict.status),
            conflict_type=conflict.conflict_type,
            details=conflict.details or {},
            resolved_by_actor_id=conflict.resolved_by_actor_id,
            created_at=conflict.created_at,
            updated_at=conflict.updated_at,
        )
