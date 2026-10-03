"""Actor application service."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.exceptions import UnauthorizedOperationError, UnknownEntityError
from semantic_memory.models import Actor, ActorStatus, ActorType
from semantic_memory.models.capabilities import Capability
from semantic_memory.repositories.actors import ActorRepository
from semantic_memory.schemas.actors import ActorEnsureRequest, ActorResponse


class ActorService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorRepository(session)

    def get(self, actor_id: uuid.UUID) -> ActorResponse:
        actor = self._actors.get(actor_id)
        if actor is None:
            raise UnknownEntityError(
                f"Actor {actor_id} was not found",
                details={"actor_id": str(actor_id)},
            )
        return self._to_response(actor)

    def find_by_key(self, key: str) -> ActorResponse | None:
        actor = self._actors.find_by_key(key)
        return None if actor is None else self._to_response(actor)

    def ensure(self, request: ActorEnsureRequest) -> ActorResponse:
        existing = self._actors.find_by_key(request.key)
        if existing is not None:
            return self._to_response(existing)
        actor = self._actors.create(
            key=request.key,
            actor_type=request.actor_type,
            status=request.status,
            capabilities=request.capabilities,
        )
        return self._to_response(actor)

    def require_active_actor(self, actor_key: str) -> Actor:
        actor = self._actors.find_by_key(actor_key)
        if actor is None:
            raise UnauthorizedOperationError(
                f"Unknown actor '{actor_key}'",
                details={"actor_key": actor_key},
            )
        if actor.status != ActorStatus.ACTIVE.value:
            raise UnauthorizedOperationError(
                f"Actor '{actor_key}' is disabled",
                details={"actor_key": actor_key, "status": actor.status},
            )
        return actor

    def require_capability(self, actor: Actor, capability: Capability | str) -> None:
        required = str(capability)
        caps = {str(item) for item in actor.capabilities}
        if Capability.ADMIN.value in caps or required in caps:
            return
        raise UnauthorizedOperationError(
            f"Actor '{actor.name}' lacks capability '{required}'",
            details={
                "actor_key": actor.name,
                "required_capability": required,
                "capabilities": sorted(caps),
            },
        )

    @staticmethod
    def _to_response(actor: Actor) -> ActorResponse:
        return ActorResponse(
            id=actor.id,
            key=actor.name,
            actor_type=ActorType(actor.actor_type),
            status=ActorStatus(actor.status),
            capabilities=[str(item) for item in actor.capabilities],
            created_at=actor.created_at,
            updated_at=actor.updated_at,
        )
