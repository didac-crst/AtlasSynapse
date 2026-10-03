"""Thin MCP-facing tool adapters for knowledge-plane operations."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from semantic_memory.exceptions import DomainError, ValidationFailedError
from semantic_memory.models.enums import ConflictStatus
from semantic_memory.schemas.conflicts import (
    DismissConflictRequest,
    FindConflictsResponse,
    MergeEntityRequest,
    ResolveConflictRequest,
)
from semantic_memory.schemas.entities import CreateEntityRequest, EntityResponse
from semantic_memory.schemas.errors import ErrorEnvelope
from semantic_memory.schemas.provenance import AddEvidenceRequest, ExplainStatementResponse
from semantic_memory.schemas.statements import (
    AssertStatementRequest,
    RetractStatementRequest,
    StatementResponse,
    SupersedeStatementRequest,
    TimelineResponse,
)
from semantic_memory.services.conflicts import ConflictService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.statements import StatementService


def _error(exc: DomainError) -> dict[str, Any]:
    return ErrorEnvelope(
        error_code=exc.error_code,
        message=exc.message,
        details=exc.details,
        request_id=exc.request_id,
        retryable=exc.retryable,
    ).model_dump(mode="json")


def _run_mutation[T: BaseModel](session: Session, operation: Callable[[], T]) -> dict[str, Any]:
    try:
        result = operation()
        session.commit()
        return result.model_dump(mode="json")
    except DomainError as exc:
        session.commit()
        return _error(exc)
    except (ValidationError, ValueError) as exc:
        session.rollback()
        return _error(
            ValidationFailedError(
                "Request validation failed",
                details={"error": str(exc)},
            )
        )
    except Exception:
        session.rollback()
        raise


class EntityMCPTools:
    """Translate MCP tool calls into entity service operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._entities = EntityService(session)

    def create_entity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._entities.create_entity(CreateEntityRequest.model_validate(payload)),
        )

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        try:
            result: EntityResponse = self._entities.get(uuid.UUID(entity_id))
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid entity id",
                    details={"entity_id": entity_id, "error": str(exc)},
                )
            )

    def merge_entity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._entities.merge_entity(MergeEntityRequest.model_validate(payload)),
        )


class StatementMCPTools:
    """Translate MCP tool calls into statement and provenance operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._statements = StatementService(session)
        self._provenance = ProvenanceService(session)
        self._conflicts = ConflictService(session)

    def assert_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._statements.assert_statement(
                AssertStatementRequest.model_validate(payload)
            ),
        )

    def get_statement(self, statement_id: str) -> dict[str, Any]:
        try:
            result: StatementResponse = self._statements.get(uuid.UUID(statement_id))
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid statement id",
                    details={"statement_id": statement_id, "error": str(exc)},
                )
            )

    def explain_statement(self, statement_id: str) -> dict[str, Any]:
        try:
            result: ExplainStatementResponse = self._provenance.explain_statement(
                uuid.UUID(statement_id)
            )
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid statement id",
                    details={"statement_id": statement_id, "error": str(exc)},
                )
            )

    def add_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._provenance.add_evidence(AddEvidenceRequest.model_validate(payload)),
        )

    def supersede_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._statements.supersede_statement(
                SupersedeStatementRequest.model_validate(payload)
            ),
        )

    def retract_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._statements.retract_statement(
                RetractStatementRequest.model_validate(payload)
            ),
        )

    def get_timeline(self, entity_id: str) -> dict[str, Any]:
        try:
            result: TimelineResponse = self._statements.get_timeline(uuid.UUID(entity_id))
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid entity id",
                    details={"entity_id": entity_id, "error": str(exc)},
                )
            )

    def find_conflicts(
        self,
        *,
        entity_id: str | None = None,
        statement_id: str | None = None,
        status: str | None = "open",
    ) -> dict[str, Any]:
        try:
            resolved_status: ConflictStatus | None
            if status is None:
                resolved_status = None
            else:
                resolved_status = ConflictStatus(status)
            result: FindConflictsResponse = self._conflicts.find_conflicts(
                entity_id=None if entity_id is None else uuid.UUID(entity_id),
                statement_id=None if statement_id is None else uuid.UUID(statement_id),
                status=resolved_status,
            )
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid conflict query",
                    details={"error": str(exc)},
                )
            )

    def resolve_conflict(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._conflicts.resolve_conflict(
                ResolveConflictRequest.model_validate(payload)
            ),
        )

    def dismiss_conflict(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            lambda: self._conflicts.dismiss_conflict(
                DismissConflictRequest.model_validate(payload)
            ),
        )
