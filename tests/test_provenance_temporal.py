"""Provenance, supersession/retraction, timeline, and operation-audit tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.exceptions import DomainViolationError, InvalidStateTransitionError
from semantic_memory.models import ActorType, OperationLog, StatementEvidence
from semantic_memory.models.enums import OperationStatus, StatementStatus
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.provenance import AddEvidenceRequest, EnsureSourceRequest, SourceInput
from semantic_memory.schemas.statements import (
    AssertStatementRequest,
    RetractStatementRequest,
    SupersedeStatementRequest,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.redaction import prepare_audit_payload
from semantic_memory.services.statements import StatementService


def _ensure_writer(session: Session, key: str = "writer") -> None:
    ActorService(session).ensure(ActorEnsureRequest(key=key, actor_type=ActorType.AGENT))


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


def _assert_description(
    session: Session,
    *,
    subject_id: uuid.UUID,
    text: str,
    valid_from: datetime | None = None,
    observed_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> uuid.UUID:
    result = StatementService(session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=idempotency_key or str(uuid.uuid4()),
            subject_entity_id=subject_id,
            predicate_key="description",
            object_string=text,
            valid_from=valid_from,
            observed_at=observed_at,
        )
    )
    return result.statement.id


def test_source_dedup_and_evidence_explain(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Memo", class_key="Document")
    statement_id = _assert_description(
        db_session,
        subject_id=doc_id,
        text="Important note",
        observed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    provenance = ProvenanceService(db_session)
    first = provenance.ensure_source(
        EnsureSourceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_system="mail",
            external_id="msg-1",
            title="Inbox",
            uri="https://example.test/msg-1",
        )
    )
    second = provenance.ensure_source(
        EnsureSourceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_system="mail",
            external_id="msg-1",
            title="Inbox again",
        )
    )
    assert first.outcome.value == "CREATE"
    assert second.outcome.value == "REUSE"
    assert first.source.id == second.source.id

    evidence = provenance.add_evidence(
        AddEvidenceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statement_id=statement_id,
            source=SourceInput(source_id=first.source.id),
            excerpt="Important note from mail",
            locator="paragraph:1",
            extraction_confidence=Decimal("0.8"),
        )
    )
    again = provenance.add_evidence(
        AddEvidenceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statement_id=statement_id,
            source=SourceInput(source_id=first.source.id),
            excerpt="Important note from mail",
            locator="paragraph:1",
        )
    )
    assert evidence.reused is False
    assert again.reused is True
    assert evidence.evidence.id == again.evidence.id

    explained = provenance.explain_statement(statement_id)
    assert explained.statement_id == statement_id
    assert explained.observed_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert len(explained.evidence) == 1
    assert explained.evidence[0].source.external_id == "msg-1"
    assert explained.evidence[0].excerpt == "Important note from mail"
    assert explained.evidence[0].locator == "paragraph:1"


def test_supersession_preserves_history_and_evidence(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Doc", class_key="Document")
    statements = StatementService(db_session)
    provenance = ProvenanceService(db_session)

    original_id = _assert_description(db_session, subject_id=doc_id, text="v1")
    provenance.add_evidence(
        AddEvidenceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statement_id=original_id,
            source=SourceInput(source_system="notes", external_id="n1", title="Note"),
            excerpt="v1 evidence",
        )
    )

    superseded = statements.supersede_statement(
        SupersedeStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            previous_statement_id=original_id,
            subject_entity_id=doc_id,
            predicate_key="description",
            object_string="v2",
        )
    )
    assert superseded.previous_statement.status == StatementStatus.SUPERSEDED
    assert superseded.previous_statement.superseded_by_statement_id == superseded.statement.id
    assert superseded.statement.status == StatementStatus.ASSERTED
    assert superseded.statement.object_string == "v2"

    explained = provenance.explain_statement(original_id)
    assert len(explained.evidence) == 1
    assert explained.evidence[0].excerpt == "v1 evidence"
    assert explained.status == StatementStatus.SUPERSEDED.value


def test_retraction_preserves_evidence(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Doc", class_key="Document")
    statement_id = _assert_description(db_session, subject_id=doc_id, text="retractable")
    ProvenanceService(db_session).add_evidence(
        AddEvidenceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statement_id=statement_id,
            source=SourceInput(uri="https://example.test/a", title="A"),
            excerpt="keep me",
        )
    )

    retracted = StatementService(db_session).retract_statement(
        RetractStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statement_id=statement_id,
        )
    )
    assert retracted.statement.status == StatementStatus.RETRACTED

    evidence_count = len(
        db_session.scalars(
            select(StatementEvidence).where(StatementEvidence.statement_id == statement_id)
        ).all()
    )
    assert evidence_count == 1
    explained = ProvenanceService(db_session).explain_statement(statement_id)
    assert explained.evidence[0].excerpt == "keep me"


def test_timeline_orders_by_validity_then_assertion(db_session: Session) -> None:
    _ensure_writer(db_session)
    subject_id = _create_entity(db_session, name="Timeline Doc", class_key="Document")
    early_obj = _create_entity(db_session, name="Early Peer", class_key="Document")
    none_obj = _create_entity(db_session, name="None Peer", class_key="Document")
    late_obj = _create_entity(db_session, name="Late Peer", class_key="Document")
    early = datetime(2020, 1, 1, tzinfo=UTC)
    late = datetime(2024, 1, 1, tzinfo=UTC)
    service = StatementService(db_session)

    def _related(object_id: uuid.UUID, *, valid_from: datetime | None = None) -> uuid.UUID:
        result = service.assert_statement(
            AssertStatementRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                subject_entity_id=subject_id,
                predicate_key="relatedTo",
                object_entity_id=object_id,
                valid_from=valid_from,
            )
        )
        return result.statement.id

    # no-validity uses asserted_at (~now) as its sort key, after historical valid_from values.
    none_id = _related(none_obj)
    early_id = _related(early_obj, valid_from=early)
    late_id = _related(late_obj, valid_from=late)
    _ = (none_id, early_id, late_id)

    timeline = service.get_timeline(subject_id)
    object_ids = [entry.statement.object_entity_id for entry in timeline.entries]
    # coalesce(valid_from, asserted_at): 2020, 2024, then no-validity asserted_at (~now).
    assert object_ids == [early_obj, late_obj, none_obj]


def test_idempotent_evidence_and_operation_audit(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Audit Doc", class_key="Document")
    statement_id = _assert_description(db_session, subject_id=doc_id, text="audited")
    key = "evidence-idem-1"
    payload = AddEvidenceRequest(
        actor_key="writer",
        request_id=uuid.uuid4(),
        idempotency_key=key,
        statement_id=statement_id,
        source=SourceInput(source_system="web", external_id="w1", title="Web"),
        excerpt="secret-ish excerpt",
    )
    first = ProvenanceService(db_session).add_evidence(payload)
    second = ProvenanceService(db_session).add_evidence(
        payload.model_copy(update={"request_id": uuid.uuid4()})
    )
    assert first.evidence.id == second.evidence.id

    logs = list(
        db_session.scalars(
            select(OperationLog).where(OperationLog.operation_name == "add_evidence")
        ).all()
    )
    assert logs
    assert all(log.status == OperationStatus.SUCCESS.value for log in logs)
    assert logs[0].request_id is not None
    # redacted retention should hide excerpt content in audit payload
    assert logs[0].request_payload is not None
    assert logs[0].request_payload.get("excerpt") == "[REDACTED]"


def test_rejected_mutation_is_audited(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Reject Doc", class_key="Document")
    statement_id = _assert_description(db_session, subject_id=doc_id, text="active")
    StatementService(db_session).retract_statement(
        RetractStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statement_id=statement_id,
        )
    )
    with pytest.raises(InvalidStateTransitionError):
        StatementService(db_session).supersede_statement(
            SupersedeStatementRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                previous_statement_id=statement_id,
                subject_entity_id=doc_id,
                predicate_key="description",
                object_string="nope",
            )
        )
    rejected = db_session.scalars(
        select(OperationLog).where(
            OperationLog.operation_name == "supersede_statement",
            OperationLog.status == OperationStatus.REJECTED.value,
        )
    ).first()
    assert rejected is not None
    assert rejected.error_code == "INVALID_STATE_TRANSITION"


def test_payload_redaction_modes() -> None:
    payload = {
        "token": "abc",
        "excerpt": "private text",
        "object_string": "visible in full",
        "nested": {"api_key": "xyz", "ok": 1},
    }
    assert prepare_audit_payload(payload, mode="none") is None
    redacted = prepare_audit_payload(payload, mode="redacted")
    assert redacted is not None
    assert redacted["token"] == "[REDACTED]"
    assert redacted["excerpt"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    full = prepare_audit_payload(payload, mode="full")
    assert full is not None
    assert full["token"] == "[REDACTED]"
    assert full["excerpt"] == "private text"
    assert full["object_string"] == "visible in full"


def test_failed_assert_does_not_poison_idempotency(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Poison Doc", class_key="Document")
    key = "retry-after-reject"
    with pytest.raises(DomainViolationError):
        StatementService(db_session).assert_statement(
            AssertStatementRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=key,
                subject_entity_id=doc_id,
                predicate_key="occurredAt",
                object_datetime=datetime.now(UTC),
            )
        )
    # Domain rejection rolls back nested reservation; retry with valid payload works.
    ok = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=key,
            subject_entity_id=doc_id,
            predicate_key="description",
            object_string="recovered",
        )
    )
    assert ok.outcome.value == "CREATE"
