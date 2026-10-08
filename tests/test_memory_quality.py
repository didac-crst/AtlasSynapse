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


def test_idempotent_replay_skips_quality_reinspect(db_session: Session) -> None:
    """Replay returns cached final response; quality must not re-run / bump counts."""
    from pydantic import BaseModel

    from semantic_memory.schemas.common import MutationEnvelope
    from semantic_memory.services.mutations import MutationRunner

    _ensure_writer(db_session)
    actor = ActorService(db_session).require_active_actor("writer")
    person = _create_entity(db_session, name="Idempotent Quality Person")
    created = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="name",
            object_string="Idempotent Quality Person",
        )
    )
    assert created.statement is not None
    row = db_session.get(Statement, created.statement.id)
    assert row is not None
    row.status = StatementStatus.SUPERSEDED.value
    row.superseded_by_statement_id = None
    db_session.flush()

    class _Probe(BaseModel):
        request_id: uuid.UUID
        quality_warnings: list[str] = []

    quality = MemoryQualityService(db_session)
    inspect_calls = {"n": 0}

    def execute() -> _Probe:
        return _Probe(request_id=uuid.uuid4())

    def post_execute(result: _Probe, operation_id: uuid.UUID) -> _Probe:
        inspect_calls["n"] += 1
        warnings = quality.inspect_after_write(
            ChangeContext(
                actor_id=actor.id,
                operation_id=operation_id,
                touched_entity_ids=[person],
                touched_statement_ids=[created.statement.id],
            )
        )
        assert warnings
        assert warnings[0].issue_id
        # operation linkage populated on first real execute
        listed = quality.list_issues(status=MemoryQualityIssueStatus.OPEN.value)
        assert listed.items[0].occurrence_count == 1
        issue = quality.get_issue(warnings[0].issue_id)
        assert issue is not None
        # trigger_operation_id is on the ORM; response schema omits it — check via repo
        orm = quality._issues.get(warnings[0].issue_id)
        assert orm is not None
        assert orm.trigger_operation_id == operation_id
        return result.model_copy(update={"quality_warnings": [str(warnings[0].issue_id)]})

    envelope = MutationEnvelope(
        actor_key="writer",
        request_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    runner = MutationRunner(db_session)
    first = runner.run(
        actor=actor,
        operation_name="probe_quality",
        request=envelope,
        response_model=_Probe,
        execute=execute,
        constraint_name="probe",
        post_execute=post_execute,
    )
    second = runner.run(
        actor=actor,
        operation_name="probe_quality",
        request=envelope,
        response_model=_Probe,
        execute=execute,
        constraint_name="probe",
        post_execute=post_execute,
    )
    assert inspect_calls["n"] == 1
    assert first.quality_warnings == second.quality_warnings
    listed = quality.list_issues(status=MemoryQualityIssueStatus.OPEN.value)
    assert listed.total == 1
    assert listed.items[0].occurrence_count == 1


def test_critical_severity_not_starved_by_sql_limit(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_entity(db_session, name="Severity Person")
    quality = MemoryQualityService(db_session)
    repo = quality._issues
    for i, severity in enumerate(
        ["info", "low", "medium", "low", "medium", "critical"],
        start=1,
    ):
        repo.create(
            issue_type="supersession_integrity",
            severity=severity,
            detector_key="supersession_integrity",
            detector_version="1",
            fingerprint=f"severity-test-{person}-{i}",
            summary=f"{severity} issue {i}",
            evidence={},
            requires_clarification=False,
            subject_entity_id=person,
        )
    warnings = quality.warnings_for_hits(entity_ids=[person], statement_ids=[], limit=5)
    assert len(warnings) == 5
    assert any(w.severity.value == "critical" for w in warnings)
    assert warnings[0].severity.value == "critical"


def test_long_acyclic_supersession_chain_is_not_a_cycle(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_entity(db_session, name="Long Chain Person")
    service = StatementService(db_session)
    previous = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="description",
            object_string="chain-0",
        )
    )
    assert previous.statement is not None
    head_id = previous.statement.id
    for i in range(1, 40):
        nxt = service.supersede_statement(
            SupersedeStatementRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                previous_statement_id=head_id,
                object_string=f"chain-{i}",
            )
        )
        head_id = nxt.statement.id
        assert nxt.quality_warnings == []

    # Seed inspect from the oldest superseded row; walk should not false-positive.
    oldest = db_session.get(Statement, previous.statement.id)
    assert oldest is not None
    warnings = MemoryQualityService(db_session).inspect_after_write(
        ChangeContext(
            touched_entity_ids=[person],
            touched_statement_ids=[oldest.id],
        )
    )
    assert warnings == []


def test_related_and_object_side_warning_surfacing(db_session: Session) -> None:
    _ensure_writer(db_session)
    subject = _create_entity(db_session, name="Related Subject")
    other = _create_entity(db_session, name="Related Object Side")
    quality = MemoryQualityService(db_session)
    repo = quality._issues
    # Issue only linked via related_entity_ids (not subject/object columns).
    related_only = repo.create(
        issue_type="supersession_integrity",
        severity="high",
        detector_key="supersession_integrity",
        detector_version="1",
        fingerprint=f"related-only-{other}",
        summary="related-only",
        evidence={},
        requires_clarification=True,
        related_entity_ids=[str(other)],
    )
    # Issue linked via object_entity_id.
    object_side = repo.create(
        issue_type="supersession_integrity",
        severity="medium",
        detector_key="supersession_integrity",
        detector_version="1",
        fingerprint=f"object-side-{other}",
        summary="object-side",
        evidence={},
        requires_clarification=False,
        object_entity_id=other,
    )
    del subject  # subject alone must not be required
    warnings = quality.warnings_for_hits(entity_ids=[other], statement_ids=[], limit=5)
    ids = {w.issue_id for w in warnings}
    assert related_only.id in ids
    assert object_side.id in ids

    context = RetrievalService(db_session).get_relevant_context(
        RelevantContextRequest(entity_id=other, limit=10)
    )
    surfaced = {w.issue_id for w in context.quality_warnings}
    assert related_only.id in surfaced
    assert object_side.id in surfaced
