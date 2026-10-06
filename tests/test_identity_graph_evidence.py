"""Graph-context supporting evidence for entity identity (PR4)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.models.enums import ActorType
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import ResolutionOutcome
from semantic_memory.schemas.identity import CandidateDecision, IdentityResolutionOutcome
from semantic_memory.schemas.statements import AssertStatementRequest
from semantic_memory.seeding.bootstrap import bootstrap_system_ontology
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.identity import IdentityService
from semantic_memory.services.statements import StatementService


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def _ensure_graph_ontology(session: Session) -> None:
    bootstrap_system_ontology(session)


def _person_class_id(session: Session) -> uuid.UUID:
    ontology = OntologyRepository(session)
    person = ontology.get_class_by_key(namespace_key="core", class_key="Person")
    assert person is not None
    return person.id


def _assert_edge(
    session: Session,
    *,
    subject_id: uuid.UUID,
    predicate_key: str,
    object_id: uuid.UUID,
) -> uuid.UUID:
    response = StatementService(session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=subject_id,
            predicate_key=predicate_key,
            object_entity_id=object_id,
        )
    )
    assert response.statement is not None
    return response.statement.id


def test_shared_graph_neighbors_stay_ambiguous_with_statement_refs(db_session: Session) -> None:
    """Didac vs Didac Cristobal with shared spouse/employer/child stays UNCERTAIN."""
    _ensure_writer(db_session)
    _ensure_graph_ontology(db_session)
    person_class = _person_class_id(db_session)
    entities = EntityService(db_session)

    spouse = entities.create_entity(_create(name="Alex Partner", class_key="Person")).entity
    employer = entities.create_entity(_create(name="Shared Corp", class_key="Organization")).entity
    child = entities.create_entity(_create(name="Kid Person", class_key="Person")).entity
    short = entities.create_entity(_create(name="Didac", class_key="Person")).entity
    assert spouse and employer and child and short
    actor = ActorService(db_session).require_active_actor("writer")
    repo = EntityRepository(db_session)
    long = repo.create(canonical_name="Didac Cristobal", created_by_actor_id=actor.id)
    repo.add_type(entity_id=long.id, class_id=person_class, asserted_by_actor_id=actor.id)

    stmt_ids: set[str] = set()
    for person_id in (short.id, long.id):
        stmt_ids.add(
            str(
                _assert_edge(
                    db_session,
                    subject_id=person_id,
                    predicate_key="employedBy",
                    object_id=employer.id,
                )
            )
        )
        stmt_ids.add(
            str(
                _assert_edge(
                    db_session,
                    subject_id=person_id,
                    predicate_key="spouseOf",
                    object_id=spouse.id,
                )
            )
        )
        stmt_ids.add(
            str(
                _assert_edge(
                    db_session,
                    subject_id=person_id,
                    predicate_key="parentOf",
                    object_id=child.id,
                )
            )
        )

    resolved = IdentityService(db_session).resolve(
        canonical_name="Didac Cristobal",
        class_id=person_class,
    )
    assert resolved.outcome == ResolutionOutcome.AMBIGUOUS
    assert resolved.identity is not None
    assert resolved.identity.resolution == IdentityResolutionOutcome.AMBIGUOUS
    assert len(resolved.identity.candidates) >= 2
    assert all(
        candidate.decision == CandidateDecision.UNCERTAIN
        for candidate in resolved.identity.candidates
    )

    graph_reasons = [
        reason
        for candidate in resolved.identity.candidates
        for reason in candidate.reasons
        if reason.signal == "shared_neighbor"
    ]
    assert graph_reasons
    ref_ids = {
        ref.split(":", 1)[1]
        for reason in graph_reasons
        for ref in reason.evidence_refs
        if ref.startswith("statement:")
    }
    assert ref_ids & stmt_ids

    # Graph evidence must not promote to MATCH.
    assert resolved.entity is None


def test_conflicting_employers_stay_uncertain_not_different(db_session: Session) -> None:
    """Different employers produce supporting conflict evidence, not decisive DIFFERENT."""
    _ensure_writer(db_session)
    _ensure_graph_ontology(db_session)
    person_class = _person_class_id(db_session)
    actors = ActorService(db_session)
    actor = actors.require_active_actor("writer")
    repo = EntityRepository(db_session)

    org_a = (
        EntityService(db_session)
        .create_entity(_create(name="Employer A", class_key="Organization"))
        .entity
    )
    org_b = (
        EntityService(db_session)
        .create_entity(_create(name="Employer B", class_key="Organization"))
        .entity
    )
    assert org_a and org_b

    left = repo.create(canonical_name="John Smith", created_by_actor_id=actor.id)
    right = repo.create(canonical_name="John Smith", created_by_actor_id=actor.id)
    repo.add_type(entity_id=left.id, class_id=person_class, asserted_by_actor_id=actor.id)
    repo.add_type(entity_id=right.id, class_id=person_class, asserted_by_actor_id=actor.id)

    left_stmt = _assert_edge(
        db_session,
        subject_id=left.id,
        predicate_key="employedBy",
        object_id=org_a.id,
    )
    right_stmt = _assert_edge(
        db_session,
        subject_id=right.id,
        predicate_key="employedBy",
        object_id=org_b.id,
    )

    resolved = IdentityService(db_session).resolve(
        canonical_name="John Smith",
        class_id=person_class,
    )
    assert resolved.outcome == ResolutionOutcome.AMBIGUOUS
    assert resolved.identity is not None
    assert all(
        candidate.decision == CandidateDecision.UNCERTAIN
        for candidate in resolved.identity.candidates
    )
    assert not any(
        candidate.decision == CandidateDecision.DIFFERENT
        for candidate in resolved.identity.candidates
    )

    conflicts = [
        reason
        for candidate in resolved.identity.candidates
        for reason in candidate.reasons
        if reason.signal == "conflicting_neighbor"
    ]
    assert conflicts
    assert all(reason.strength.value == "supporting" for reason in conflicts)
    conflict_refs = {ref for reason in conflicts for ref in reason.evidence_refs}
    assert f"statement:{left_stmt}" in conflict_refs or f"statement:{right_stmt}" in conflict_refs


def test_graph_evidence_deduplicates_by_statement(db_session: Session) -> None:
    """The same underlying statement must not produce duplicate graph evidence rows."""
    _ensure_writer(db_session)
    _ensure_graph_ontology(db_session)
    person_class = _person_class_id(db_session)
    service = EntityService(db_session)

    org = service.create_entity(_create(name="Only Corp", class_key="Organization")).entity
    left = service.create_entity(_create(name="Pat", class_key="Person")).entity
    assert org and left
    actor = ActorService(db_session).require_active_actor("writer")
    repo = EntityRepository(db_session)
    right = repo.create(canonical_name="Pat Example", created_by_actor_id=actor.id)
    repo.add_type(entity_id=right.id, class_id=person_class, asserted_by_actor_id=actor.id)

    stmt_id = _assert_edge(
        db_session,
        subject_id=left.id,
        predicate_key="employedBy",
        object_id=org.id,
    )
    _assert_edge(
        db_session,
        subject_id=right.id,
        predicate_key="employedBy",
        object_id=org.id,
    )

    resolved = IdentityService(db_session).resolve(
        canonical_name="Pat Example",
        class_id=person_class,
    )
    for candidate in resolved.identity.candidates or []:
        shared = [
            reason
            for reason in candidate.reasons
            if reason.signal == "shared_neighbor" and f"statement:{stmt_id}" in reason.evidence_refs
        ]
        if not shared:
            continue
        fingerprints = [
            (
                reason.signal,
                reason.namespace,
                reason.value,
                tuple(sorted(reason.evidence_refs)),
            )
            for reason in shared
        ]
        assert len(fingerprints) == len(set(fingerprints))


def _create(*, name: str, class_key: str):
    from semantic_memory.schemas.entities import CreateEntityRequest

    return CreateEntityRequest(
        actor_key="writer",
        request_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        canonical_name=name,
        class_key=class_key,
    )
