"""Thin MCP-facing tool adapters for entity operations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.exceptions import DomainError
from semantic_memory.schemas.entities import CreateEntityRequest, EntityResponse
from semantic_memory.schemas.errors import ErrorEnvelope
from semantic_memory.services.entities import EntityService


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
            return self._error(exc)

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        try:
            result: EntityResponse = self._entities.get(uuid.UUID(entity_id))
            return result.model_dump(mode="json")
        except DomainError as exc:
            return self._error(exc)

    @staticmethod
    def _error(exc: DomainError) -> dict[str, Any]:
        return ErrorEnvelope(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
            request_id=exc.request_id,
            retryable=exc.retryable,
        ).model_dump(mode="json")
