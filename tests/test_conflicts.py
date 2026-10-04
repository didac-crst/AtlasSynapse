"""Conflict coexistence, retrieval, resolution metadata, and entity merge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.exceptions import InvalidStateTransitionError
from semantic_memory.models import ActorType, Statement
from semantic_memory.models.enums import ConflictStatus, EntityStatus, StatementStatus
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.conflicts import (
    DismissConflictRequest,
    MergeEntityRequest,
    ResolveConflictRequest,
)
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.statements import AssertStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.conflicts import ConflictService
from semantic_memory.services.entities import EntityService
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


def test_cardinality_conflict_preserves_both_statements(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Conflict Doc", class_key="Document")
    service = StatementService(db_session)
    first = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="First",
        )
    )
    second = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="Second",
        )
    )
    assert first.outcome.value == "CREATE"
    assert second.outcome.value == "CREATE"
    assert second.conflict_ids
    count = db_session.scalar(
        select(func.count())
        .select_from(Statement)
        .where(
            Statement.subject_entity_id == doc_id,
            Statement.status == StatementStatus.ASSERTED.value,
        )
    )
    assert count == 2
    found = ConflictService(db_session).find_conflicts(entity_id=doc_id)
    assert len(found.conflicts) == 1
    assert found.conflicts[0].conflict_type == "cardinality"
    assert found.conflicts[0].status == ConflictStatus.OPEN


def test_non_overlapping_validity_does_not_conflict(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Interval Doc", class_key="Document")
    service = StatementService(db_session)
    service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="Old",
            valid_from=datetime(2020, 1, 1, tzinfo=UTC),
            valid_to=datetime(2020, 12, 31, tzinfo=UTC),
        )
    )
    second = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="New",
            valid_from=datetime(2021, 1, 1, tzinfo=UTC),
            valid_to=datetime(2021, 12, 31, tzinfo=UTC),
        )
    )
    assert second.conflict_ids == []
    found = ConflictService(db_session).find_conflicts(entity_id=doc_id)
    assert found.conflicts == []


def test_resolve_and_dismiss_conflict_metadata(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Resolve Doc", class_key="Document")
    service = StatementService(db_session)
    service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="A",
        )
    )
    second = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="B",
        )
    )
    conflict_id = second.conflict_ids[0]
    conflicts = ConflictService(db_session)
    resolved = conflicts.resolve_conflict(
        ResolveConflictRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            conflict_id=conflict_id,
            resolution_note="kept both pending review",
        )
    )
    assert resolved.conflict.status == ConflictStatus.RESOLVED
    assert resolved.conflict.details["resolution_note"] == "kept both pending review"
    assert resolved.conflict.resolved_by_actor_id is not None
    # Statements remain asserted; resolve only updates conflict metadata.
    count = db_session.scalar(
        select(func.count())
        .select_from(Statement)
        .where(
            Statement.subject_entity_id == doc_id,
            Statement.status == StatementStatus.ASSERTED.value,
        )
    )
    assert count == 2

    # Create another conflict pair via a third name, then dismiss.
    third = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="name",
            object_string="C",
        )
    )
    dismiss_id = third.conflict_ids[0]
    dismissed = conflicts.dismiss_conflict(
        DismissConflictRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            conflict_id=dismiss_id,
            resolution_note="noise",
        )
    )
    assert dismissed.conflict.status == ConflictStatus.DISMISSED
    with pytest.raises(InvalidStateTransitionError):
        conflicts.dismiss_conflict(
            DismissConflictRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                conflict_id=dismiss_id,
            )
        )


def test_merge_entity_preserves_history(db_session: Session) -> None:
    _ensure_writer(db_session)
    source_id = _create_entity(db_session, name="Source Doc", class_key="Document")
    target_id = _create_entity(db_session, name="Target Doc", class_key="Document")
    StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=source_id,
            predicate_key="description",
            object_string="keeps subject id",
        )
    )
    merged = EntityService(db_session).merge_entity(
        MergeEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_entity_id=source_id,
            target_entity_id=target_id,
        )
    )
    assert merged.source.status == EntityStatus.MERGED
    assert merged.source.merged_into_entity_id == target_id
    assert merged.target.status == EntityStatus.ACTIVE
    assert "Source Doc" in merged.target.aliases
    statement = db_session.scalars(
        select(Statement).where(Statement.subject_entity_id == target_id)
    ).one()
    assert statement.subject_entity_id == target_id
    assert (
        db_session.scalars(
            select(Statement).where(Statement.subject_entity_id == source_id)
        ).first()
        is None
    )
    # Source name redirects to the surviving target via transferred alias.
    reuse = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Source Doc",
            class_key="Document",
        )
    )
    assert reuse.outcome.value == "REUSE"
    assert reuse.entity is not None
    assert reuse.entity.id == target_id
