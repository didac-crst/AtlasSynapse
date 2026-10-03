"""Statement assertion validation, reuse, and concurrency tests."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from semantic_memory.exceptions import (
    CardinalityViolationError,
    DomainViolationError,
    IdempotencyKeyReusedError,
    InvalidLiteralTypeError,
    RangeViolationError,
    UnknownPredicateError,
    ValidationFailedError,
)
from semantic_memory.models import ActorType, Statement
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.statements import AssertionOutcome, AssertStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.statements import StatementService


def _ensure_writer(session: Session, key: str = "writer") -> None:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
        )
    )


def _create_entity(session: Session, *, name: str, class_key: str) -> uuid.UUID:
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


def _assert_request(
    *,
    subject_entity_id: uuid.UUID,
    predicate_key: str,
    idempotency_key: str | None = None,
    **object_fields: object,
) -> AssertStatementRequest:
    return AssertStatementRequest(
        actor_key="writer",
        request_id=uuid.uuid4(),
        idempotency_key=idempotency_key or str(uuid.uuid4()),
        subject_entity_id=subject_entity_id,
        predicate_key=predicate_key,
        **object_fields,  # type: ignore[arg-type]
    )


def test_assert_string_and_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Spec", class_key="Document")
    service = StatementService(db_session)

    first = service.assert_statement(
        _assert_request(
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="  Atlas Synapse  ",
            confidence=Decimal("0.875"),
        )
    )
    second = service.assert_statement(
        _assert_request(
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="atlas synapse",
            confidence=Decimal("0.9"),
        )
    )

    assert first.outcome == AssertionOutcome.CREATE
    assert second.outcome == AssertionOutcome.REUSE
    assert first.statement.id == second.statement.id
    assert first.statement.object_string == "Atlas Synapse"
    assert first.statement.confidence == Decimal("0.8750")
    assert first.statement.normalized_object == "string:atlas synapse"


def test_assert_entity_object_with_inheritance(db_session: Session) -> None:
    _ensure_writer(db_session)
    event_id = _create_entity(db_session, name="Kickoff", class_key="Event")
    person_id = _create_entity(db_session, name="Didac", class_key="Person")
    result = StatementService(db_session).assert_statement(
        _assert_request(
            subject_entity_id=event_id,
            predicate_key="actor",
            object_entity_id=person_id,
        )
    )
    assert result.outcome == AssertionOutcome.CREATE
    assert result.statement.object_entity_id == person_id


def test_domain_violation(db_session: Session) -> None:
    _ensure_writer(db_session)
    person_id = _create_entity(db_session, name="Didac", class_key="Person")
    with pytest.raises(DomainViolationError) as exc:
        StatementService(db_session).assert_statement(
            _assert_request(
                subject_entity_id=person_id,
                predicate_key="occurredAt",
                object_datetime=datetime.now(UTC),
            )
        )
    assert exc.value.error_code == "DOMAIN_VIOLATION"


def test_range_violation(db_session: Session) -> None:
    _ensure_writer(db_session)
    event_id = _create_entity(db_session, name="Meetup", class_key="Event")
    place_id = _create_entity(db_session, name="Barcelona", class_key="Place")
    with pytest.raises(RangeViolationError) as exc:
        StatementService(db_session).assert_statement(
            _assert_request(
                subject_entity_id=event_id,
                predicate_key="actor",
                object_entity_id=place_id,
            )
        )
    assert exc.value.error_code == "RANGE_VIOLATION"


def test_invalid_literal_type(db_session: Session) -> None:
    _ensure_writer(db_session)
    event_id = _create_entity(db_session, name="Talk", class_key="Event")
    with pytest.raises(InvalidLiteralTypeError) as exc:
        StatementService(db_session).assert_statement(
            _assert_request(
                subject_entity_id=event_id,
                predicate_key="occurredAt",
                object_string="tomorrow",
            )
        )
    assert exc.value.error_code == "INVALID_LITERAL_TYPE"


def test_unknown_predicate(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Doc", class_key="Document")
    with pytest.raises(UnknownPredicateError) as exc:
        StatementService(db_session).assert_statement(
            _assert_request(
                subject_entity_id=doc_id,
                predicate_key="notAPredicate",
                object_string="x",
            )
        )
    assert exc.value.error_code == "UNKNOWN_PREDICATE"


def test_invalid_temporal_interval(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Doc", class_key="Document")
    start = datetime.now(UTC)
    with pytest.raises(ValidationFailedError) as exc:
        StatementService(db_session).assert_statement(
            _assert_request(
                subject_entity_id=doc_id,
                predicate_key="description",
                object_string="late",
                valid_from=start,
                valid_to=start - timedelta(days=1),
            )
        )
    assert exc.value.error_code == "VALIDATION_FAILED"


def test_confidence_bounds(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Doc", class_key="Document")
    with pytest.raises(ValidationFailedError):
        StatementService(db_session).assert_statement(
            _assert_request(
                subject_entity_id=doc_id,
                predicate_key="description",
                object_string="bad confidence",
                confidence=Decimal("1.5"),
            )
        )


def test_cardinality_one_violation(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Doc", class_key="Document")
    service = StatementService(db_session)
    service.assert_statement(
        _assert_request(
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="First",
        )
    )
    with pytest.raises(CardinalityViolationError) as exc:
        service.assert_statement(
            _assert_request(
                subject_entity_id=doc_id,
                predicate_key="name",
                object_string="Second",
            )
        )
    assert exc.value.error_code == "CARDINALITY_VIOLATION"


def test_idempotent_retry_and_key_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Doc", class_key="Document")
    service = StatementService(db_session)
    key = "idem-assert-name"
    first = service.assert_statement(
        _assert_request(
            subject_entity_id=doc_id,
            predicate_key="description",
            object_string="stable",
            idempotency_key=key,
        )
    )
    second = service.assert_statement(
        _assert_request(
            subject_entity_id=doc_id,
            predicate_key="description",
            object_string="stable",
            idempotency_key=key,
        )
    )
    assert first.statement.id == second.statement.id
    assert first.outcome == AssertionOutcome.CREATE
    assert second.outcome == AssertionOutcome.CREATE

    with pytest.raises(IdempotencyKeyReusedError):
        service.assert_statement(
            _assert_request(
                subject_entity_id=doc_id,
                predicate_key="description",
                object_string="changed",
                idempotency_key=key,
            )
        )


def test_parallel_assert_only_one_statement(engine: Engine) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    setup = SessionLocal()
    try:
        ActorService(setup).ensure(
            ActorEnsureRequest(key="parallel-writer", actor_type=ActorType.AGENT)
        )
        setup.commit()
        doc = EntityService(setup).create_entity(
            CreateEntityRequest(
                actor_key="parallel-writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                canonical_name=f"Parallel Doc {uuid.uuid4()}",
                class_key="Document",
            )
        )
        setup.commit()
        assert doc.entity is not None
        subject_id = doc.entity.id
    finally:
        setup.close()

    barrier = threading.Barrier(8)
    label = f"shared-name-{uuid.uuid4()}"

    def _worker() -> str:
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            result = StatementService(session).assert_statement(
                AssertStatementRequest(
                    actor_key="parallel-writer",
                    request_id=uuid.uuid4(),
                    idempotency_key=str(uuid.uuid4()),
                    subject_entity_id=subject_id,
                    predicate_key="description",
                    object_string=label,
                )
            )
            session.commit()
            assert result.outcome in {AssertionOutcome.CREATE, AssertionOutcome.REUSE}
            return str(result.statement.id)
        finally:
            session.close()

    ids: set[str] = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_worker) for _ in range(8)]
        for future in as_completed(futures):
            ids.add(future.result())
    assert len(ids) == 1

    verify = SessionLocal()
    try:
        count = verify.scalar(
            select(func.count())
            .select_from(Statement)
            .where(
                Statement.subject_entity_id == subject_id,
                Statement.normalized_object == f"string:{label.casefold()}",
            )
        )
        assert count == 1
    finally:
        verify.close()
