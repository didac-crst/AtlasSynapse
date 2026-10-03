"""Thin MCP-facing tool adapters for entity and statement operations."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from semantic_memory.exceptions import DomainError, ValidationFailedError
from semantic_memory.schemas.entities import CreateEntityRequest, EntityResponse
from semantic_memory.schemas.errors import ErrorEnvelope
from semantic_memory.schemas.statements import AssertStatementRequest, StatementResponse
from semantic_memory.services.entities import EntityService
from semantic_memory.services.statements import StatementService


def _error(exc: DomainError) -> dict[str, Any]:
    return ErrorEnvelope(
        error_code=exc.error_code,
        message=exc.message,
        details=exc.details,
        request_id=exc.request_id,
        retryable=exc.retryable,
    ).model_dump(mode="json")


class EntityMCPTools:
    """Translate MCP tool calls into entity service operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._entities = EntityService(session)

    def create_entity(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = CreateEntityRequest.model_validate(payload)
            result = self._entities.create_entity(request)
            self._session.commit()
            return result.model_dump(mode="json")
        except DomainError as exc:
            self._session.rollback()
            return _error(exc)
        except (ValidationError, ValueError) as exc:
            self._session.rollback()
            return _error(
                ValidationFailedError(
                    "Request validation failed",
                    details={"error": str(exc)},
                )
            )
        except Exception:
            self._session.rollback()
            raise

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


class StatementMCPTools:
    """Translate MCP tool calls into statement service operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._statements = StatementService(session)

    def assert_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = AssertStatementRequest.model_validate(payload)
            result = self._statements.assert_statement(request)
            self._session.commit()
            return result.model_dump(mode="json")
        except DomainError as exc:
            self._session.rollback()
            return _error(exc)
        except (ValidationError, ValueError) as exc:
            self._session.rollback()
            return _error(
                ValidationFailedError(
                    "Request validation failed",
                    details={"error": str(exc)},
                )
            )
        except Exception:
            self._session.rollback()
            raise

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
