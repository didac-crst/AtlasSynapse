"""Phase 7: rich events as typed entities and statements — no domain SQL tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from semantic_memory.models import ActorType
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.statements import AssertStatementRequest
from semantic_memory.seeding import ensure_rich_event_models
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.statements import StatementService


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def _create(session: Session, *, name: str, class_key: str) -> uuid.UUID:
    result = EntityService(session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=name,
            class_key=class_key,
        )
    )
    assert result.entity is not None
    return result.entity.id


def _assert(
    session: Session,
    *,
    subject_id: uuid.UUID,
    predicate_key: str,
    object_entity_id: uuid.UUID | None = None,
    object_string: str | None = None,
    object_datetime: datetime | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> uuid.UUID:
    kwargs: dict[str, object] = {
        "actor_key": "writer",
        "request_id": uuid.uuid4(),
        "idempotency_key": str(uuid.uuid4()),
        "subject_entity_id": subject_id,
        "predicate_key": predicate_key,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }
    if object_entity_id is not None:
        kwargs["object_entity_id"] = object_entity_id
    if object_string is not None:
        kwargs["object_string"] = object_string
    if object_datetime is not None:
        kwargs["object_datetime"] = object_datetime
    result = StatementService(session).assert_statement(AssertStatementRequest(**kwargs))
    return result.statement.id


def test_ensure_rich_event_models_is_idempotent_and_table_free(db_session: Session) -> None:
    first = ensure_rich_event_models(db_session)
    second = ensure_rich_event_models(db_session)
    assert first["created_classes"] >= 1 or first["created_parents"] >= 1
    assert second["created_classes"] == 0
    assert second["created_parents"] == 0
    assert second["created_domains"] == 0
    table_names = set(inspect(db_session.get_bind()).get_table_names())
    assert "employment" not in table_names
    assert "residence" not in table_names
    assert "move_event" not in table_names


def test_employment_residence_move_and_participation(db_session: Session) -> None:
    _ensure_writer(db_session)
    ensure_rich_event_models(db_session)

    person = _create(db_session, name="Didac", class_key="Person")
    org = _create(db_session, name="Airbus", class_key="Organization")
    place = _create(db_session, name="Barcelona", class_key="Place")
    project = _create(db_session, name="AtlasSynapse", class_key="Project")

    employment = _create(db_session, name="Didac@Airbus", class_key="Employment")
    _assert(
        db_session, subject_id=employment, predicate_key="hasParticipant", object_entity_id=person
    )
    _assert(db_session, subject_id=employment, predicate_key="relatedTo", object_entity_id=org)
    _assert(
        db_session,
        subject_id=employment,
        predicate_key="startedAt",
        object_datetime=datetime(2024, 1, 1, tzinfo=UTC),
    )
    _assert(
        db_session,
        subject_id=employment,
        predicate_key="description",
        object_string="Senior engineer",
    )

    residence = _create(db_session, name="Didac lives in Barcelona", class_key="Residence")
    _assert(
        db_session, subject_id=residence, predicate_key="hasParticipant", object_entity_id=person
    )
    _assert(db_session, subject_id=residence, predicate_key="locatedAt", object_entity_id=place)
    _assert(
        db_session,
        subject_id=residence,
        predicate_key="startedAt",
        object_datetime=datetime(2022, 6, 1, tzinfo=UTC),
    )

    move = _create(db_session, name="Move to Barcelona", class_key="MoveEvent")
    _assert(db_session, subject_id=move, predicate_key="actor", object_entity_id=person)
    _assert(
        db_session,
        subject_id=move,
        predicate_key="occurredAt",
        object_datetime=datetime(2022, 5, 30, tzinfo=UTC),
    )
    _assert(db_session, subject_id=move, predicate_key="locatedAt", object_entity_id=place)

    participation = _create(
        db_session, name="Didac on AtlasSynapse", class_key="ProjectParticipation"
    )
    _assert(
        db_session,
        subject_id=participation,
        predicate_key="hasParticipant",
        object_entity_id=person,
    )
    _assert(
        db_session, subject_id=participation, predicate_key="relatedTo", object_entity_id=project
    )

    experiment = _create(db_session, name="Run-42", class_key="ExperimentRun")
    _assert(db_session, subject_id=experiment, predicate_key="actor", object_entity_id=person)
    _assert(
        db_session,
        subject_id=experiment,
        predicate_key="startedAt",
        object_datetime=datetime(2026, 3, 1, tzinfo=UTC),
    )

    decision = _create(db_session, name="Choose PostgreSQL", class_key="Decision")
    _assert(db_session, subject_id=decision, predicate_key="actor", object_entity_id=person)
    _assert(
        db_session,
        subject_id=decision,
        predicate_key="occurredAt",
        object_datetime=datetime(2026, 1, 15, tzinfo=UTC),
    )

    observation = _create(db_session, name="Latency spike", class_key="Observation")
    _assert(
        db_session,
        subject_id=observation,
        predicate_key="occurredAt",
        object_datetime=datetime(2026, 2, 1, tzinfo=UTC),
    )
    _assert(
        db_session,
        subject_id=observation,
        predicate_key="description",
        object_string="p99 crossed budget",
    )
