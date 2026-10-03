"""Entity creation, identity resolution, and idempotency tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    DuplicateEntityError,
    IdempotencyKeyReusedError,
    UnauthorizedOperationError,
    UnknownClassError,
)
from semantic_memory.models import ActorStatus, ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES, Capability
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import (
    CreateEntityRequest,
    ExternalReferenceInput,
    ResolutionOutcome,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService


def _ensure_writer(session: Session, key: str = "writer") -> None:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )


def _create_request(
    *,
    name: str,
    class_key: str,
    actor_key: str = "writer",
    aliases: list[str] | None = None,
    external_reference: ExternalReferenceInput | None = None,
    idempotency_key: str | None = None,
) -> CreateEntityRequest:
    return CreateEntityRequest(
        actor_key=actor_key,
        request_id=uuid.uuid4(),
        idempotency_key=idempotency_key or str(uuid.uuid4()),
        canonical_name=name,
        class_key=class_key,
        aliases=aliases or [],
        external_reference=external_reference,
    )


def test_create_entity_and_exact_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)

    first = service.create_entity(_create_request(name="Didac", class_key="Person"))
    second = service.create_entity(_create_request(name="Didac", class_key="Person"))

    assert first.outcome == ResolutionOutcome.CREATE
    assert first.entity is not None
    assert second.outcome == ResolutionOutcome.REUSE
    assert second.entity is not None
    assert first.entity.id == second.entity.id
    assert "Person" in {item.class_key for item in first.entity.types}


def test_alias_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)

    created = service.create_entity(
        _create_request(name="Didac Costa", class_key="Person", aliases=["Didac"])
    )
    reused = service.create_entity(_create_request(name="Didac", class_key="Person"))

    assert created.outcome == ResolutionOutcome.CREATE
    assert reused.outcome == ResolutionOutcome.REUSE
    assert created.entity is not None
    assert reused.entity is not None
    assert created.entity.id == reused.entity.id


def test_external_reference_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    ref = ExternalReferenceInput(source_system="crm", external_id="person-1")

    created = service.create_entity(
        _create_request(name="Didac", class_key="Person", external_reference=ref)
    )
    reused = service.create_entity(
        _create_request(
            name="Someone Else",
            class_key="Person",
            external_reference=ref,
        )
    )

    assert created.outcome == ResolutionOutcome.CREATE
    assert reused.outcome == ResolutionOutcome.REUSE
    assert created.entity is not None
    assert reused.entity is not None
    assert created.entity.id == reused.entity.id


def test_ambiguity_returns_candidates_without_merge(db_session: Session) -> None:
    _ensure_writer(db_session)
    actors = ActorService(db_session)
    actor = actors.require_active_actor("writer")
    ontology = OntologyRepository(db_session)
    person = ontology.get_class_by_key(namespace_key="core", class_key="Person")
    assert person is not None

    repo = EntityRepository(db_session)
    left = repo.create(canonical_name="Didac", created_by_actor_id=actor.id)
    right = repo.create(canonical_name="Didac", created_by_actor_id=actor.id)
    repo.add_type(entity_id=left.id, class_id=person.id, asserted_by_actor_id=actor.id)
    repo.add_type(entity_id=right.id, class_id=person.id, asserted_by_actor_id=actor.id)

    result = EntityService(db_session).create_entity(
        _create_request(name="Didac", class_key="Person")
    )

    assert result.outcome == ResolutionOutcome.AMBIGUOUS
    assert result.entity is None
    assert {item.id for item in result.candidates} == {left.id, right.id}


def test_unknown_class_error(db_session: Session) -> None:
    _ensure_writer(db_session)
    with pytest.raises(UnknownClassError) as exc:
        EntityService(db_session).create_entity(
            _create_request(name="Widget", class_key="NotAClass")
        )
    assert exc.value.error_code == "UNKNOWN_CLASS"


def test_disabled_actor_error(db_session: Session) -> None:
    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="disabled-writer",
            actor_type=ActorType.AGENT,
            status=ActorStatus.DISABLED,
            capabilities=[Capability.KNOWLEDGE_WRITE.value],
        )
    )
    with pytest.raises(UnauthorizedOperationError) as exc:
        EntityService(db_session).create_entity(
            _create_request(name="Didac", class_key="Person", actor_key="disabled-writer")
        )
    assert exc.value.error_code == "UNAUTHORIZED_OPERATION"


def test_duplicate_alias_does_not_silent_merge(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    service.create_entity(
        _create_request(name="Didac Costa", class_key="Person", aliases=["Didac"])
    )
    with pytest.raises(DuplicateEntityError) as exc:
        service.create_entity(
            _create_request(name="Didac Garcia", class_key="Person", aliases=["Didac"])
        )
    assert exc.value.error_code == "DUPLICATE_ENTITY"


def test_idempotent_retry_and_key_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    key = "idem-create-didac"
    first = service.create_entity(
        _create_request(name="Didac", class_key="Person", idempotency_key=key)
    )
    second = service.create_entity(
        _create_request(name="Didac", class_key="Person", idempotency_key=key)
    )
    assert first.outcome == ResolutionOutcome.CREATE
    assert second.outcome == ResolutionOutcome.CREATE
    assert first.entity is not None
    assert second.entity is not None
    assert first.entity.id == second.entity.id

    with pytest.raises(IdempotencyKeyReusedError) as exc:
        service.create_entity(
            _create_request(name="Airbus", class_key="Organization", idempotency_key=key)
        )
    assert exc.value.error_code == "IDEMPOTENCY_KEY_REUSED"


def test_acceptance_scenario(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)

    didac = service.create_entity(_create_request(name="Didac", class_key="Person"))
    airbus = service.create_entity(_create_request(name="Airbus", class_key="Organization"))
    didac_again = service.create_entity(_create_request(name="Didac", class_key="Person"))
    airbus_again = service.create_entity(_create_request(name="Airbus", class_key="Organization"))

    assert didac.outcome == ResolutionOutcome.CREATE
    assert airbus.outcome == ResolutionOutcome.CREATE
    assert didac_again.outcome == ResolutionOutcome.REUSE
    assert airbus_again.outcome == ResolutionOutcome.REUSE
    assert didac.entity is not None
    assert airbus.entity is not None
    assert didac_again.entity is not None
    assert airbus_again.entity is not None
    assert didac.entity.id == didac_again.entity.id
    assert airbus.entity.id == airbus_again.entity.id

    actors = ActorService(db_session)
    actor = actors.require_active_actor("writer")
    ontology = OntologyRepository(db_session)
    person = ontology.get_class_by_key(namespace_key="core", class_key="Person")
    assert person is not None
    repo = EntityRepository(db_session)
    twin = repo.create(canonical_name="Didac", created_by_actor_id=actor.id)
    repo.add_type(entity_id=twin.id, class_id=person.id, asserted_by_actor_id=actor.id)

    ambiguous = service.create_entity(_create_request(name="Didac", class_key="Person"))
    assert ambiguous.outcome == ResolutionOutcome.AMBIGUOUS
    assert ambiguous.entity is None
    assert len(ambiguous.candidates) >= 2

    with pytest.raises(UnknownClassError):
        service.create_entity(_create_request(name="X", class_key="Spaceship"))

    actors.ensure(
        ActorEnsureRequest(
            key="disabled-writer",
            actor_type=ActorType.AGENT,
            status=ActorStatus.DISABLED,
            capabilities=[Capability.KNOWLEDGE_WRITE.value],
        )
    )
    with pytest.raises(UnauthorizedOperationError):
        service.create_entity(
            _create_request(name="Y", class_key="Person", actor_key="disabled-writer")
        )
