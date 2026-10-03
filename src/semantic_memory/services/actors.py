"""Actor application service."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.exceptions import (
    InvalidStateTransitionError,
    UnauthorizedOperationError,
    UnknownEntityError,
)
from semantic_memory.models import Actor, ActorStatus, ActorType
from semantic_memory.models.capabilities import Capability
from semantic_memory.repositories.actors import ActorRepository
from semantic_memory.schemas.actors import ActorEnsureRequest, ActorResponse


class ActorService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._actors = ActorRepository(session)
        self._settings = settings or get_settings()

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
        """Create or return an actor using the configured safe capability set.

        Callers cannot grant ``admin``. Capability grants always come from
        settings. Existing actors are never mutated; mismatches conflict.
        """
        requested_caps = [str(item) for item in request.capabilities]
        if Capability.ADMIN.value in requested_caps:
            raise UnauthorizedOperationError(
                "Callers cannot grant the admin capability",
                details={"actor_key": request.key},
            )

        default_caps = list(self._settings.default_actor_capabilities)
        if Capability.ADMIN.value in default_caps:
            raise UnauthorizedOperationError(
                "Configured default actor capabilities must not include admin",
                details={"actor_key": request.key},
            )

        existing = self._actors.find_by_key(request.key)
        if existing is not None:
            existing_caps = sorted(str(item) for item in existing.capabilities)
            if existing.status != request.status.value:
                raise InvalidStateTransitionError(
                    "Existing actor status conflicts with ensure request",
                    details={
                        "actor_key": request.key,
                        "existing_status": existing.status,
                        "requested_status": request.status.value,
                    },
                )
            if requested_caps and sorted(requested_caps) != existing_caps:
                raise InvalidStateTransitionError(
                    "Existing actor capabilities conflict with ensure request",
                    details={
                        "actor_key": request.key,
                        "existing_capabilities": existing_caps,
                        "requested_capabilities": sorted(requested_caps),
                    },
                )
            return self._to_response(existing)

        if requested_caps and sorted(requested_caps) != sorted(default_caps):
            raise InvalidStateTransitionError(
                "Requested capabilities conflict with configured defaults",
                details={
                    "actor_key": request.key,
                    "requested_capabilities": sorted(requested_caps),
                    "configured_capabilities": sorted(default_caps),
                },
            )

        actor = self._actors.create(
            key=request.key,
            actor_type=request.actor_type,
            status=request.status,
            capabilities=default_caps,
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
