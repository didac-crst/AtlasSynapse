"""Actor service tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from semantic_memory.exceptions import InvalidStateTransitionError, UnauthorizedOperationError
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
        )
    )
    assert created.id == again.id
    assert service.find_by_key("chatgpt") is not None
    assert service.get(created.id).key == "chatgpt"
    assert Capability.ADMIN.value not in created.capabilities


def test_ensure_actor_rejects_admin_capability_grant(db_session: Session) -> None:
    service = ActorService(db_session)
    with pytest.raises(UnauthorizedOperationError) as exc:
        service.ensure(
            ActorEnsureRequest(
                key="evil",
                actor_type=ActorType.AGENT,
                capabilities=[Capability.ADMIN.value],
            )
        )
    assert exc.value.error_code == "UNAUTHORIZED_OPERATION"
    assert service.find_by_key("evil") is None


def test_ensure_actor_conflict_on_capability_mismatch(db_session: Session) -> None:
    service = ActorService(db_session)
    service.ensure(
        ActorEnsureRequest(
            key="stable",
            actor_type=ActorType.AGENT,
        )
    )
    with pytest.raises(InvalidStateTransitionError) as exc:
        service.ensure(
            ActorEnsureRequest(
                key="stable",
                actor_type=ActorType.AGENT,
                capabilities=[Capability.KNOWLEDGE_READ.value],
            )
        )
    assert exc.value.error_code == "INVALID_STATE_TRANSITION"


def test_ensure_actor_conflict_on_status_mismatch(db_session: Session) -> None:
    service = ActorService(db_session)
    service.ensure(
        ActorEnsureRequest(
            key="statused",
            actor_type=ActorType.AGENT,
            status=ActorStatus.ACTIVE,
        )
    )
    with pytest.raises(InvalidStateTransitionError):
        service.ensure(
            ActorEnsureRequest(
                key="statused",
                actor_type=ActorType.AGENT,
                status=ActorStatus.DISABLED,
            )
        )


def test_disabled_actor_rejected(db_session: Session) -> None:
    service = ActorService(db_session)
    service.ensure(
        ActorEnsureRequest(
            key="disabled-bot",
            actor_type=ActorType.AGENT,
            status=ActorStatus.DISABLED,
        )
    )
    with pytest.raises(UnauthorizedOperationError) as exc:
        service.require_active_actor("disabled-bot")
    assert exc.value.error_code == "UNAUTHORIZED_OPERATION"
