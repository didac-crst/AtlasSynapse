"""Critical database constraint tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semantic_memory.models import (
    Actor,
    Entity,
    EntityStatus,
    ExternalReference,
    OntologyAlias,
    OntologyNamespace,
    OntologyPredicate,
    Source,
    Statement,
    StatementStatus,
)


def _system_actor(session: Session) -> Actor:
    actor = session.scalar(select(Actor).where(Actor.name == "system"))
    assert actor is not None
    return actor


def _any_predicate(session: Session) -> OntologyPredicate:
    predicate = session.scalar(select(OntologyPredicate).limit(1))
    assert predicate is not None
    return predicate


def _make_entity(session: Session, actor: Actor, name: str) -> Entity:
    entity = Entity(
        id=uuid.uuid4(),
        canonical_name=name,
        status=EntityStatus.ACTIVE.value,
        created_by_actor_id=actor.id,
    )
    session.add(entity)
    session.flush()
    return entity


def test_statement_requires_exactly_one_object(db_session: Session) -> None:
    actor = _system_actor(db_session)
    subject = _make_entity(db_session, actor, "Subject A")
    predicate = _any_predicate(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                Statement(
                    id=uuid.uuid4(),
                    subject_entity_id=subject.id,
                    predicate_id=predicate.id,
                    status=StatementStatus.ASSERTED.value,
                    asserted_at=datetime.now(UTC),
                    actor_id=actor.id,
                )
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            other = _make_entity(db_session, actor, "Subject B")
            db_session.add(
                Statement(
                    id=uuid.uuid4(),
                    subject_entity_id=subject.id,
                    predicate_id=predicate.id,
                    object_entity_id=other.id,
                    object_string="also set",
                    status=StatementStatus.ASSERTED.value,
                    asserted_at=datetime.now(UTC),
                    actor_id=actor.id,
                )
            )
            db_session.flush()


def test_statement_confidence_and_valid_interval_bounds(db_session: Session) -> None:
    actor = _system_actor(db_session)
    subject = _make_entity(db_session, actor, "Timed Subject")
    predicate = _any_predicate(db_session)
    now = datetime.now(UTC)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                Statement(
                    id=uuid.uuid4(),
                    subject_entity_id=subject.id,
                    predicate_id=predicate.id,
                    object_string="x",
                    confidence=Decimal("1.5"),
                    status=StatementStatus.ASSERTED.value,
                    asserted_at=now,
                    actor_id=actor.id,
                )
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                Statement(
                    id=uuid.uuid4(),
                    subject_entity_id=subject.id,
                    predicate_id=predicate.id,
                    object_string="x",
                    valid_from=now,
                    valid_to=now - timedelta(days=1),
                    status=StatementStatus.ASSERTED.value,
                    asserted_at=now,
                    actor_id=actor.id,
                )
            )
            db_session.flush()


def test_ontology_alias_target_exclusivity(db_session: Session) -> None:
    namespace = db_session.scalar(select(OntologyNamespace).where(OntologyNamespace.key == "core"))
    assert namespace is not None
    predicate = _any_predicate(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                OntologyAlias(
                    id=uuid.uuid4(),
                    namespace_id=namespace.id,
                    alias="bad-alias",
                    target_type="class",
                    predicate_id=predicate.id,
                    class_id=None,
                )
            )
            db_session.flush()


def test_external_reference_unique_system_id(db_session: Session) -> None:
    actor = _system_actor(db_session)
    entity = _make_entity(db_session, actor, "External Entity")
    db_session.add(
        ExternalReference(
            id=uuid.uuid4(),
            entity_id=entity.id,
            source_system="crm",
            external_id="abc-123",
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                ExternalReference(
                    id=uuid.uuid4(),
                    entity_id=entity.id,
                    source_system="crm",
                    external_id="abc-123",
                )
            )
            db_session.flush()


def test_source_reliability_bounds(db_session: Session) -> None:
    actor = _system_actor(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                Source(
                    id=uuid.uuid4(),
                    title="Unreliable",
                    reliability=Decimal("2.0"),
                    created_by_actor_id=actor.id,
                )
            )
            db_session.flush()


def test_actor_type_check_constraint(db_session: Session) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO actor (id, name, actor_type, status, capabilities) "
                    "VALUES (:id, 'bad', 'alien', 'active', '[]'::jsonb)"
                ),
                {"id": str(uuid.uuid4())},
            )
            db_session.flush()


def test_idempotency_unique_actor_key(db_session: Session) -> None:
    actor = _system_actor(db_session)
    db_session.execute(
        text(
            "INSERT INTO idempotency_record "
            "(id, actor_id, idempotency_key, request_hash, operation_name) "
            "VALUES (:id, :actor_id, 'key-1', 'hash-1', 'create_entity')"
        ),
        {"id": str(uuid.uuid4()), "actor_id": str(actor.id)},
    )
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO idempotency_record "
                    "(id, actor_id, idempotency_key, request_hash, operation_name) "
                    "VALUES (:id, :actor_id, 'key-1', 'hash-2', 'create_entity')"
                ),
                {"id": str(uuid.uuid4()), "actor_id": str(actor.id)},
            )
            db_session.flush()
