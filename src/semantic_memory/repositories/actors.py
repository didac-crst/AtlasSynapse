"""Actor persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import Actor, ActorStatus, ActorType


class ActorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, actor_id: uuid.UUID) -> Actor | None:
        return self._session.get(Actor, actor_id)

    def find_by_key(self, key: str) -> Actor | None:
        """Look up an actor by its key (stored in ``actor.name``)."""
        return self._session.scalar(select(Actor).where(Actor.name == key))

    def create(
        self,
        *,
        key: str,
        actor_type: ActorType | str,
        status: ActorStatus | str = ActorStatus.ACTIVE,
        capabilities: list[str] | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> Actor:
        actor = Actor(
            id=actor_id or uuid.uuid4(),
            name=key,
            actor_type=str(actor_type),
            status=str(status),
            capabilities=list(capabilities or []),
        )
        self._session.add(actor)
        self._session.flush()
        return actor
