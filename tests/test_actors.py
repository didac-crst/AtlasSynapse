"""Actor service tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from semantic_memory.exceptions import UnauthorizedOperationError
from semantic_memory.models import ActorStatus, ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES, Capability
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.services.actors import ActorService


def test_ensure_actor_creates_and_finds_by_key(db_session: Session) -> None:
    service = ActorService(db_session)
    created = service.ensure(
        ActorEnsureRequest(
            key="chatgpt",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    again = service.ensure(
        ActorEnsureRequest(
            key="chatgpt",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    assert created.id == again.id
    assert service.find_by_key("chatgpt") is not None
    assert service.get(created.id).key == "chatgpt"


def test_disabled_actor_rejected(db_session: Session) -> None:
    service = ActorService(db_session)
    service.ensure(
        ActorEnsureRequest(
            key="disabled-bot",
            actor_type=ActorType.AGENT,
            status=ActorStatus.DISABLED,
            capabilities=[Capability.KNOWLEDGE_WRITE.value],
        )
    )
    with pytest.raises(UnauthorizedOperationError) as exc:
        service.require_active_actor("disabled-bot")
    assert exc.value.error_code == "UNAUTHORIZED_OPERATION"
