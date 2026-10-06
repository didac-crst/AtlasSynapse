"""Entity creation, identity resolution, and idempotency tests."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from semantic_memory.exceptions import (
    DuplicateEntityError,
    IdempotencyKeyReusedError,
    UnauthorizedOperationError,
    UnknownClassError,
)
from semantic_memory.models import ActorStatus, ActorType, Entity, EntityType, OperationLog
from semantic_memory.models.enums import AliasIdentityStrength, OperationStatus
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.conflicts import MergeEntityRequest
from semantic_memory.schemas.entities import (
    AddEntityAliasRequest,
    CreateEntityRequest,
    ExternalReferenceInput,
    ResolutionOutcome,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.identity import IdentityService


def _ensure_writer(session: Session, key: str = "writer") -> None:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
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
    """Exact supporting name match must not auto-collapse to REUSE."""
    _ensure_writer(db_session)
    service = EntityService(db_session)

    first = service.create_entity(_create_request(name="Didac", class_key="Person"))
    second = service.create_entity(_create_request(name="Didac", class_key="Person"))

    assert first.outcome == ResolutionOutcome.CREATE
    assert first.entity is not None
    assert second.outcome == ResolutionOutcome.AMBIGUOUS
    assert second.entity is None
    assert {item.id for item in second.candidates} == {first.entity.id}
    assert "Person" in {item.class_key for item in first.entity.types}


def test_alias_reuse(db_session: Session) -> None:
    """Supporting aliases alone must not auto-collapse."""
    _ensure_writer(db_session)
    service = EntityService(db_session)

    created = service.create_entity(
        _create_request(name="Didac Costa", class_key="Person", aliases=["Didac"])
    )
    second = service.create_entity(_create_request(name="Didac", class_key="Person"))

    assert created.outcome == ResolutionOutcome.CREATE
    assert second.outcome == ResolutionOutcome.AMBIGUOUS
    assert created.entity is not None
    assert {item.id for item in second.candidates} == {created.entity.id}


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


def test_indexed_identity_queries_reuse_and_ambiguity(db_session: Session) -> None:
    _ensure_writer(db_session)
    actor = ActorService(db_session).require_active_actor("writer")
    ontology = OntologyRepository(db_session)
    person = ontology.get_class_by_key(namespace_key="core", class_key="Person")
    org = ontology.get_class_by_key(namespace_key="core", class_key="Organization")
    assert person is not None
    assert org is not None

    repo = EntityRepository(db_session)
    person_entity = repo.create(canonical_name="Atlas Corp", created_by_actor_id=actor.id)
    repo.add_type(entity_id=person_entity.id, class_id=person.id, asserted_by_actor_id=actor.id)
    repo.add_alias(entity_id=person_entity.id, alias="Atlas")

    org_entity = repo.create(canonical_name="Atlas Corp", created_by_actor_id=actor.id)
    repo.add_type(entity_id=org_entity.id, class_id=org.id, asserted_by_actor_id=actor.id)

    identity = IdentityService(db_session)
    person_hit = identity.resolve(canonical_name="atlas", class_id=person.id)
    assert person_hit.outcome == ResolutionOutcome.AMBIGUOUS
    assert person_hit.identity is not None
    assert person_hit.identity.resolution.value == "AMBIGUOUS"
    assert {item.id for item in person_hit.candidates} == {person_entity.id}

    ambiguous = identity.resolve(canonical_name="Atlas Corp")
    assert ambiguous.outcome == ResolutionOutcome.AMBIGUOUS
    assert {item.id for item in ambiguous.candidates} == {person_entity.id, org_entity.id}


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
    first = service.create_entity(
        _create_request(name="Alpha Loop", class_key="Project", aliases=["SharedMark"])
    )
    assert first.entity is not None
    with pytest.raises(DuplicateEntityError) as exc:
        service.create_entity(
            _create_request(name="Beta Loop", class_key="Project", aliases=["SharedMark"])
        )
    assert exc.value.error_code == "DUPLICATE_ENTITY"


def test_shared_nickname_near_name_is_ambiguous(db_session: Session) -> None:
    """Didac Costa vs Didac Garcia must not silent-merge; near-name forces review."""
    _ensure_writer(db_session)
    service = EntityService(db_session)
    first = service.create_entity(
        _create_request(name="Didac Costa", class_key="Person", aliases=["Didac"])
    )
    assert first.entity is not None
    second = service.create_entity(_create_request(name="Didac Garcia", class_key="Person"))
    assert second.outcome == ResolutionOutcome.AMBIGUOUS
    assert {item.id for item in second.candidates} == {first.entity.id}


def test_near_name_is_ambiguous_not_create(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    short = service.create_entity(_create_request(name="Didac", class_key="Person"))
    assert short.entity is not None

    longer = service.create_entity(_create_request(name="Didac Cristobal", class_key="Person"))
    assert longer.outcome == ResolutionOutcome.AMBIGUOUS
    assert longer.entity is None
    assert {item.id for item in longer.candidates} == {short.entity.id}
    assert longer.candidates[0].match_reason == "near_name"


def test_add_entity_alias_enables_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    created = service.create_entity(_create_request(name="Didac Cristobal", class_key="Person"))
    assert created.entity is not None

    updated = service.add_entity_alias(
        AddEntityAliasRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entity_id=created.entity.id,
            alias="Didac",
            identity_strength=AliasIdentityStrength.AUTHORITATIVE,
        )
    )
    assert "Didac" in updated.aliases

    reused = service.create_entity(_create_request(name="Didac", class_key="Person"))
    assert reused.outcome == ResolutionOutcome.REUSE
    assert reused.entity is not None
    assert reused.entity.id == created.entity.id


def test_neighborhood_follows_merge_redirect(db_session: Session) -> None:
    """Statement FKs stay on the merged row; neighborhood resolves to the survivor."""
    from semantic_memory.schemas.statements import AssertStatementRequest
    from semantic_memory.services.retrieval import RetrievalService
    from semantic_memory.services.statements import StatementService

    _ensure_writer(db_session)
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    keeper = entities.create_entity(_create_request(name="Keeper", class_key="Person"))
    duplicate = entities.create_entity(_create_request(name="Other Person", class_key="Person"))
    peer = entities.create_entity(_create_request(name="Peer", class_key="Person"))
    assert keeper.entity and duplicate.entity and peer.entity

    statements.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=duplicate.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer.entity.id,
        )
    )
    entities.merge_entity(
        MergeEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_entity_id=duplicate.entity.id,
            target_entity_id=keeper.entity.id,
        )
    )

    neighborhood = RetrievalService(db_session).get_entity_neighborhood(keeper.entity.id)
    neighbor_ids = {edge.neighbor_entity_id for edge in neighborhood.edges}
    assert peer.entity.id in neighbor_ids

    peer_view = RetrievalService(db_session).get_entity_neighborhood(peer.entity.id)
    assert any(edge.neighbor_entity_id == keeper.entity.id for edge in peer_view.edges)

    # Physical statement FKs are reassigned to the survivor for graph exporters.
    from sqlalchemy import select

    from semantic_memory.models import Statement

    moved = db_session.scalars(
        select(Statement).where(Statement.subject_entity_id == keeper.entity.id)
    ).all()
    assert moved
    assert not db_session.scalars(
        select(Statement).where(Statement.subject_entity_id == duplicate.entity.id)
    ).all()


def test_merge_transfers_aliases_for_redirect(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    left = service.create_entity(_create_request(name="Alpha Loop", class_key="Project"))
    right = service.create_entity(_create_request(name="Beta Loop", class_key="Project"))
    assert left.entity is not None
    assert right.entity is not None

    merged = service.merge_entity(
        MergeEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_entity_id=right.entity.id,
            target_entity_id=left.entity.id,
        )
    )
    assert "Beta Loop" in merged.target.aliases
    assert any(
        entry.alias == "Beta Loop"
        and entry.identity_strength == AliasIdentityStrength.AUTHORITATIVE
        for entry in merged.target.alias_entries
    )
    reused = service.create_entity(_create_request(name="Beta Loop", class_key="Project"))
    assert reused.outcome == ResolutionOutcome.REUSE
    assert reused.entity is not None
    assert reused.entity.id == left.entity.id


def test_merge_preserves_supporting_alias_strength(db_session: Session) -> None:
    """Supporting aliases must not become authoritative during merge."""
    _ensure_writer(db_session)
    service = EntityService(db_session)
    target = service.create_entity(_create_request(name="Keeper", class_key="Person"))
    source = service.create_entity(_create_request(name="Merged Person", class_key="Person"))
    assert target.entity is not None
    assert source.entity is not None

    service.add_entity_alias(
        AddEntityAliasRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entity_id=source.entity.id,
            alias="Nickname Only",
            identity_strength=AliasIdentityStrength.SUPPORTING,
        )
    )
    merged = service.merge_entity(
        MergeEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_entity_id=source.entity.id,
            target_entity_id=target.entity.id,
        )
    )
    by_alias = {entry.alias: entry.identity_strength for entry in merged.target.alias_entries}
    assert by_alias["Merged Person"] == AliasIdentityStrength.AUTHORITATIVE
    assert by_alias["Nickname Only"] == AliasIdentityStrength.SUPPORTING

    ambiguous = service.create_entity(_create_request(name="Nickname Only", class_key="Person"))
    assert ambiguous.outcome == ResolutionOutcome.AMBIGUOUS

    """Confirmed alias must MATCH; this is the original Didac Cristobal case."""
    _ensure_writer(db_session)
    service = EntityService(db_session)
    created = service.create_entity(_create_request(name="Didac", class_key="Person"))
    assert created.entity is not None

    service.add_entity_alias(
        AddEntityAliasRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entity_id=created.entity.id,
            alias="Didac Cristobal",
            identity_strength=AliasIdentityStrength.AUTHORITATIVE,
        )
    )

    reused = service.create_entity(_create_request(name="Didac Cristobal", class_key="Person"))
    assert reused.outcome == ResolutionOutcome.REUSE
    assert reused.entity is not None
    assert reused.entity.id == created.entity.id


def test_supporting_alias_name_match_is_ambiguous(db_session: Session) -> None:
    """Supporting name/alias evidence must not auto-collapse John Smith."""
    _ensure_writer(db_session)
    service = EntityService(db_session)
    created = service.create_entity(_create_request(name="John Smith", class_key="Person"))
    assert created.outcome == ResolutionOutcome.CREATE
    assert created.entity is not None
    assert any(
        entry.alias == "John Smith" and entry.identity_strength == AliasIdentityStrength.SUPPORTING
        for entry in created.entity.alias_entries
    )

    second = service.create_entity(_create_request(name="John Smith", class_key="Person"))
    assert second.outcome == ResolutionOutcome.AMBIGUOUS
    assert second.entity is None
    assert {item.id for item in second.candidates} == {created.entity.id}


def test_conflicting_decisive_identity_is_ambiguous(db_session: Session) -> None:
    """Two decisive SAME candidates must not let ordering pick a winner."""
    _ensure_writer(db_session)
    actors = ActorService(db_session)
    actor = actors.require_active_actor("writer")
    ontology = OntologyRepository(db_session)
    person = ontology.get_class_by_key(namespace_key="core", class_key="Person")
    assert person is not None

    repo = EntityRepository(db_session)
    left = repo.create(canonical_name="Didac Left", created_by_actor_id=actor.id)
    right = repo.create(canonical_name="Didac Right", created_by_actor_id=actor.id)
    repo.add_type(entity_id=left.id, class_id=person.id, asserted_by_actor_id=actor.id)
    repo.add_type(entity_id=right.id, class_id=person.id, asserted_by_actor_id=actor.id)
    repo.add_alias(
        entity_id=left.id,
        alias="Didac Cristobal",
        identity_strength=AliasIdentityStrength.AUTHORITATIVE.value,
    )
    repo.add_alias(
        entity_id=right.id,
        alias="Didac Cristobal",
        identity_strength=AliasIdentityStrength.AUTHORITATIVE.value,
    )

    result = EntityService(db_session).create_entity(
        _create_request(name="Didac Cristobal", class_key="Person")
    )
    assert result.outcome == ResolutionOutcome.AMBIGUOUS
    assert result.entity is None
    assert {item.id for item in result.candidates} == {left.id, right.id}

    identity = IdentityService(db_session).resolve(
        canonical_name="Didac Cristobal", class_id=person.id
    )
    assert identity.identity is not None
    assert identity.identity.resolution.value == "AMBIGUOUS"
    same = [c for c in identity.identity.candidates if c.decision.value == "SAME"]
    assert len(same) == 2


def test_failed_create_rolls_back_idempotency_and_allows_retry(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    key = "idem-rollback-retry"

    original = service._create_new_entity

    def _boom(**kwargs: object) -> object:
        raise RuntimeError("simulated failure")

    service._create_new_entity = _boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated failure"):
        service.create_entity(
            _create_request(name="Rollback Person", class_key="Person", idempotency_key=key)
        )

    service._create_new_entity = original  # type: ignore[method-assign]
    retried = service.create_entity(
        _create_request(name="Rollback Person", class_key="Person", idempotency_key=key)
    )
    assert retried.outcome == ResolutionOutcome.CREATE
    assert retried.entity is not None


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


def test_parallel_create_only_one_entity(engine: Engine) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    setup = SessionLocal()
    try:
        ActorService(setup).ensure(
            ActorEnsureRequest(
                key="parallel-writer",
                actor_type=ActorType.AGENT,
            )
        )
        setup.commit()
        ontology = OntologyRepository(setup)
        person = ontology.get_class_by_key(namespace_key="core", class_key="Person")
        assert person is not None
        class_id = person.id
    finally:
        setup.close()

    barrier = threading.Barrier(8)
    name = f"Parallel Person {uuid.uuid4()}"

    def _worker() -> str:
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            result = EntityService(session).create_entity(
                CreateEntityRequest(
                    actor_key="parallel-writer",
                    request_id=uuid.uuid4(),
                    idempotency_key=str(uuid.uuid4()),
                    canonical_name=name,
                    class_key="Person",
                )
            )
            session.commit()
            assert result.outcome in {
                ResolutionOutcome.CREATE,
                ResolutionOutcome.REUSE,
                ResolutionOutcome.AMBIGUOUS,
            }
            if result.outcome == ResolutionOutcome.AMBIGUOUS:
                assert result.entity is None
                return "ambiguous"
            assert result.entity is not None
            return str(result.entity.id)
        finally:
            session.close()

    entity_ids: set[str] = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_worker) for _ in range(8)]
        for future in as_completed(futures):
            value = future.result()
            if value != "ambiguous":
                entity_ids.add(value)

    assert len(entity_ids) == 1

    verify = SessionLocal()
    try:
        count = verify.scalar(
            select(func.count())
            .select_from(Entity)
            .join(EntityType, EntityType.entity_id == Entity.id)
            .where(
                EntityType.class_id == class_id,
                Entity.canonical_name == name,
            )
        )
        assert count == 1
    finally:
        verify.close()


def test_acceptance_scenario(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)

    didac = service.create_entity(_create_request(name="Didac", class_key="Person"))
    airbus = service.create_entity(_create_request(name="Airbus", class_key="Organization"))
    didac_again = service.create_entity(_create_request(name="Didac", class_key="Person"))
    airbus_again = service.create_entity(_create_request(name="Airbus", class_key="Organization"))

    assert didac.outcome == ResolutionOutcome.CREATE
    assert airbus.outcome == ResolutionOutcome.CREATE
    assert didac_again.outcome == ResolutionOutcome.AMBIGUOUS
    assert airbus_again.outcome == ResolutionOutcome.AMBIGUOUS
    assert didac.entity is not None
    assert airbus.entity is not None
    assert didac_again.entity is None
    assert airbus_again.entity is None
    assert {item.id for item in didac_again.candidates} == {didac.entity.id}
    assert {item.id for item in airbus_again.candidates} == {airbus.entity.id}

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
        )
    )
    with pytest.raises(UnauthorizedOperationError):
        service.create_entity(
            _create_request(name="Y", class_key="Person", actor_key="disabled-writer")
        )


def test_create_entity_records_operation_log_success(db_session: Session) -> None:
    _ensure_writer(db_session)
    request = _create_request(name="Audited Person", class_key="Person")
    result = EntityService(db_session).create_entity(request)
    assert result.outcome == ResolutionOutcome.CREATE
    log = db_session.scalars(
        select(OperationLog).where(OperationLog.request_id == request.request_id)
    ).one()
    assert log.operation_name == "create_entity"
    assert log.status == OperationStatus.SUCCESS.value
    assert log.error_code is None


def test_create_entity_records_operation_log_rejection(db_session: Session) -> None:
    _ensure_writer(db_session)
    request = _create_request(name="Ghost", class_key="Spaceship")
    with pytest.raises(UnknownClassError):
        EntityService(db_session).create_entity(request)
    log = db_session.scalars(
        select(OperationLog).where(OperationLog.request_id == request.request_id)
    ).one()
    assert log.operation_name == "create_entity"
    assert log.status == OperationStatus.REJECTED.value
    assert log.error_code == "UNKNOWN_CLASS"
