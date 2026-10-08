"""Memory quality v1: supersession integrity, dedupe, mutation warnings, latency."""

from __future__ import annotations

import time
import uuid

from sqlalchemy.orm import Session

from semantic_memory.models import ActorType, Statement
from semantic_memory.models.enums import MemoryQualityIssueStatus, StatementStatus
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.memory_quality import ChangeContext
from semantic_memory.schemas.retrieval import RelevantContextRequest
from semantic_memory.schemas.statements import (
    AssertStatementRequest,
    SupersedeStatementRequest,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.memory_quality import MemoryQualityService
from semantic_memory.services.memory_quality.fingerprint import build_quality_fingerprint
from semantic_memory.services.retrieval import RetrievalService
from semantic_memory.services.statements import StatementService


def _ensure_writer(session: Session, key: str = "writer") -> None:
    ActorService(session).ensure(ActorEnsureRequest(key=key, actor_type=ActorType.AGENT))


def _create_entity(session: Session, *, name: str, class_key: str = "Person") -> uuid.UUID:
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


def test_fingerprint_excludes_detector_version() -> None:
    eid = uuid.uuid4()
    sid = uuid.uuid4()
    a = build_quality_fingerprint(
        issue_type="supersession_integrity",
        detector_key="supersession_integrity",
        entity_ids=[eid],
        statement_ids=[sid],
    )
    b = build_quality_fingerprint(
        issue_type="supersession_integrity",
        detector_key="supersession_integrity",
        entity_ids=[eid],
        statement_ids=[sid],
    )
    assert a == b
    assert "v2" not in a


def test_missing_successor_opens_quality_issue_and_dedupes(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_entity(db_session, name="Quality Person")
    service = StatementService(db_session)
    created = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="name",
            object_string="Quality Person",
        )
    )
    assert created.statement is not None
    row = db_session.get(Statement, created.statement.id)
    assert row is not None
    row.status = StatementStatus.SUPERSEDED.value
    row.superseded_by_statement_id = None
    db_session.flush()

    quality = MemoryQualityService(db_session)
    ctx = ChangeContext(
        operation_name="test",
        touched_entity_ids=[person],
        touched_statement_ids=[created.statement.id],
    )
    warnings1 = quality.inspect_after_write(ctx)
    assert len(warnings1) == 1
    assert warnings1[0].type.value == "supersession_integrity"
    assert warnings1[0].requires_clarification is True

    warnings2 = quality.inspect_after_write(ctx)
    assert len(warnings2) == 1
    assert warnings2[0].issue_id == warnings1[0].issue_id

    listed = quality.list_issues(status=MemoryQualityIssueStatus.OPEN.value)
    assert listed.total == 1
    assert listed.items[0].occurrence_count == 2


def test_healthy_supersede_does_not_open_issue(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_entity(db_session, name="Healthy Supersede")
    service = StatementService(db_session)
    first = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="name",
            object_string="Old",
        )
    )
    assert first.statement is not None
    second = service.supersede_statement(
        SupersedeStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            previous_statement_id=first.statement.id,
            object_string="New",
        )
    )
    assert second.quality_warnings == []
    listed = MemoryQualityService(db_session).list_issues(
        status=MemoryQualityIssueStatus.OPEN.value
    )
    assert listed.total == 0


def test_retrieval_surfaces_relevant_quality_warning(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_entity(db_session, name="Retrieval Warn Person")
    service = StatementService(db_session)
    created = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="name",
            object_string="Retrieval Warn Person",
        )
    )
    assert created.statement is not None
    row = db_session.get(Statement, created.statement.id)
    assert row is not None
    row.status = StatementStatus.SUPERSEDED.value
    row.superseded_by_statement_id = None
    db_session.flush()

    MemoryQualityService(db_session).inspect_after_write(
        ChangeContext(
            touched_entity_ids=[person],
            touched_statement_ids=[created.statement.id],
        )
    )
    context = RetrievalService(db_session).get_relevant_context(
        RelevantContextRequest(entity_id=person, limit=10)
    )
    assert context.quality_warnings
    assert context.quality_warnings[0].type.value == "supersession_integrity"


def test_assert_latency_with_quality_hook(db_session: Session) -> None:
    """Baseline measurement: post-write quality must stay cheap on healthy writes."""
    _ensure_writer(db_session)
    person = _create_entity(db_session, name="Latency Person")
    service = StatementService(db_session)
    samples: list[float] = []
    for i in range(5):
        start = time.perf_counter()
        result = service.assert_statement(
            AssertStatementRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                subject_entity_id=person,
                predicate_key="description",
                object_string=f"latency sample {i}",
            )
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        samples.append(elapsed_ms)
        assert result.quality_warnings == []
    avg_ms = sum(samples) / len(samples)
    # Soft ceiling for local CI; neighborhood inspect must not dominate healthy writes.
    assert avg_ms < 2000, f"assert+quality avg too high: {avg_ms:.1f}ms samples={samples}"
