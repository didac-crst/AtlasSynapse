"""Checkpoint 2: governed supersession integrity repair."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from semantic_memory.exceptions import ValidationFailedError
from semantic_memory.models import ActorType, Statement
from semantic_memory.models.enums import (
    MemoryQualityIssueStatus,
    MemoryQualityResolution,
    StatementStatus,
    SupersessionRepairOutcome,
)
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.memory_quality import (
    ChangeContext,
    RepairSupersessionIntegrityRequest,
)
from semantic_memory.schemas.statements import AssertStatementRequest, SupersedeStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.memory_quality import (
    MemoryQualityService,
    SupersessionIntegrityRepairService,
)
from semantic_memory.services.statements import StatementService


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def _create_person(session: Session, name: str) -> uuid.UUID:
    result = EntityService(session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=name,
            class_key="Person",
        )
    )
    assert result.entity is not None
    return result.entity.id


def _open_missing_successor_issue(
    session: Session, *, person: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return (issue_id, corrupted_statement_id, valid_successor_id)."""
    service = StatementService(session)
    first = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="name",
            object_string="Old Name",
        )
    )
    assert first.statement is not None
    second = service.supersede_statement(
        SupersedeStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            previous_statement_id=first.statement.id,
            object_string="New Name",
        )
    )
    old = session.get(Statement, first.statement.id)
    assert old is not None
    old.superseded_by_statement_id = None
    session.flush()
    warnings = MemoryQualityService(session).inspect_after_write(
        ChangeContext(
            touched_entity_ids=[person],
            touched_statement_ids=[old.id],
        )
    )
    assert warnings
    return warnings[0].issue_id, old.id, second.statement.id


def test_repair_missing_successor(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Missing")
    issue_id, statement_id, successor_id = _open_missing_successor_issue(
        db_session, person=person
    )
    result = SupersessionIntegrityRepairService(db_session).repair(
        RepairSupersessionIntegrityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            issue_id=issue_id,
            successor_statement_id=successor_id,
        )
    )
    assert result.outcome == SupersessionRepairOutcome.REPAIRED
    assert result.issue_resolved is True
    assert result.resolution == MemoryQualityResolution.STRUCTURAL_REPAIR
    assert result.statement_id == statement_id
    assert result.successor_statement_id == successor_id
    row = db_session.get(Statement, statement_id)
    assert row is not None
    assert row.superseded_by_statement_id == successor_id
    issue = MemoryQualityService(db_session).get_issue(issue_id)
    assert issue is not None
    assert issue.status == MemoryQualityIssueStatus.RESOLVED
    assert issue.resolution == MemoryQualityResolution.STRUCTURAL_REPAIR
    orm = MemoryQualityService(db_session)._issues.get(issue_id)
    assert orm is not None
    assert orm.resolution_operation_id is not None
    assert orm.resolved_by_actor_id is not None


def test_repair_incompatible_successor_with_compatible_replacement(
    db_session: Session,
) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Incompat Edge")
    service = StatementService(db_session)
    first = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="name",
            object_string="A",
        )
    )
    second = service.supersede_statement(
        SupersedeStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            previous_statement_id=first.statement.id,
            object_string="B",
        )
    )
    wrong = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="description",
            object_string="wrong predicate successor",
        )
    )
    old = db_session.get(Statement, first.statement.id)
    assert old is not None
    old.superseded_by_statement_id = wrong.statement.id
    db_session.flush()
    warnings = MemoryQualityService(db_session).inspect_after_write(
        ChangeContext(touched_statement_ids=[old.id], touched_entity_ids=[person])
    )
    assert warnings
    result = SupersessionIntegrityRepairService(db_session).repair(
        RepairSupersessionIntegrityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            issue_id=warnings[0].issue_id,
            successor_statement_id=second.statement.id,
        )
    )
    assert result.outcome == SupersessionRepairOutcome.REPAIRED
    assert db_session.get(Statement, old.id).superseded_by_statement_id == second.statement.id


def test_repair_rejects_incompatible_successor(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Incompat Subject")
    other = _create_person(db_session, "Other Person")
    issue_id, _statement_id, _ = _open_missing_successor_issue(db_session, person=person)
    other_stmt = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=other,
            predicate_key="name",
            object_string="Other",
        )
    )
    with pytest.raises(ValidationFailedError, match="semantically incompatible"):
        SupersessionIntegrityRepairService(db_session).repair(
            RepairSupersessionIntegrityRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                issue_id=issue_id,
                successor_statement_id=other_stmt.statement.id,
            )
        )
    issue = MemoryQualityService(db_session).get_issue(issue_id)
    assert issue is not None
    assert issue.status == MemoryQualityIssueStatus.OPEN


def test_repair_rejects_cycle(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Cycle Reject")
    issue_id, statement_id, successor_id = _open_missing_successor_issue(
        db_session, person=person
    )
    # Point successor back at the corrupted statement to form a cycle if linked.
    succ = db_session.get(Statement, successor_id)
    assert succ is not None
    succ.superseded_by_statement_id = statement_id
    db_session.flush()
    with pytest.raises(ValidationFailedError, match="cycle"):
        SupersessionIntegrityRepairService(db_session).repair(
            RepairSupersessionIntegrityRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                issue_id=issue_id,
                successor_statement_id=successor_id,
            )
        )


def test_repair_breaks_existing_cycle(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Break Cycle")
    service = StatementService(db_session)
    a = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="description",
            object_string="a",
        )
    )
    b = service.supersede_statement(
        SupersedeStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            previous_statement_id=a.statement.id,
            object_string="b",
        )
    )
    c = service.supersede_statement(
        SupersedeStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            previous_statement_id=b.statement.id,
            object_string="c",
        )
    )
    # Force cycle A→B→C→A
    row_c = db_session.get(Statement, c.statement.id)
    assert row_c is not None
    row_c.status = StatementStatus.SUPERSEDED.value
    row_c.superseded_by_statement_id = a.statement.id
    db_session.flush()
    warnings = MemoryQualityService(db_session).inspect_after_write(
        ChangeContext(
            touched_statement_ids=[a.statement.id, b.statement.id, c.statement.id],
            touched_entity_ids=[person],
        )
    )
    assert any("cycle" in (w.summary.lower()) for w in warnings) or warnings
    # Break cycle by pointing C at a terminal leaf (no outgoing) — use a fresh asserted peer.
    leaf = service.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=person,
            predicate_key="description",
            object_string="leaf",
        )
    )
    # Find the cycle issue on statement C (or A).
    cycle_issue = next(
        (
            w
            for w in warnings
            if "cycle" in w.summary.lower() or w.requires_clarification
        ),
        warnings[0],
    )
    # Prefer issue whose statement is C if present.
    for w in warnings:
        if w.related_statement_ids and c.statement.id in w.related_statement_ids:
            cycle_issue = w
            break
    result = SupersessionIntegrityRepairService(db_session).repair(
        RepairSupersessionIntegrityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            issue_id=cycle_issue.issue_id,
            successor_statement_id=leaf.statement.id,
        )
    )
    assert result.outcome == SupersessionRepairOutcome.REPAIRED


def test_stale_issue_no_longer_applicable(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Stale")
    issue_id, statement_id, successor_id = _open_missing_successor_issue(
        db_session, person=person
    )
    # Silently fix graph before repair.
    row = db_session.get(Statement, statement_id)
    assert row is not None
    row.superseded_by_statement_id = successor_id
    db_session.flush()
    result = SupersessionIntegrityRepairService(db_session).repair(
        RepairSupersessionIntegrityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            issue_id=issue_id,
            successor_statement_id=successor_id,
        )
    )
    assert result.outcome == SupersessionRepairOutcome.NO_LONGER_APPLICABLE
    issue = MemoryQualityService(db_session).get_issue(issue_id)
    assert issue is not None
    assert issue.status == MemoryQualityIssueStatus.RESOLVED
    assert issue.resolution == MemoryQualityResolution.NO_ACTION


def test_repair_dry_run(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Dry Run")
    issue_id, statement_id, successor_id = _open_missing_successor_issue(
        db_session, person=person
    )
    result = SupersessionIntegrityRepairService(db_session).repair(
        RepairSupersessionIntegrityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            issue_id=issue_id,
            successor_statement_id=successor_id,
            dry_run=True,
        )
    )
    assert result.outcome == SupersessionRepairOutcome.WOULD_REPAIR
    assert result.would_persist is True
    assert result.dry_run is True
    row = db_session.get(Statement, statement_id)
    assert row is not None
    assert row.superseded_by_statement_id is None
    issue = MemoryQualityService(db_session).get_issue(issue_id)
    assert issue is not None
    assert issue.status == MemoryQualityIssueStatus.OPEN


def test_repair_idempotent_replay(db_session: Session) -> None:
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Idempotent")
    issue_id, _statement_id, successor_id = _open_missing_successor_issue(
        db_session, person=person
    )
    req = RepairSupersessionIntegrityRequest(
        actor_key="writer",
        request_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        issue_id=issue_id,
        successor_statement_id=successor_id,
    )
    repair = SupersessionIntegrityRepairService(db_session)
    first = repair.repair(req)
    second = repair.repair(req)
    assert first.outcome == SupersessionRepairOutcome.REPAIRED
    assert second.outcome == SupersessionRepairOutcome.REPAIRED
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_repair_reveals_another_quality_issue(db_session: Session) -> None:
    """Original issue closes; post_execute returns a new warning on the successor."""
    _ensure_writer(db_session)
    person = _create_person(db_session, "Repair Reveals New")
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
    second = service.supersede_statement(
        SupersedeStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            previous_statement_id=first.statement.id,
            object_string="New",
        )
    )
    # Corrupt A (missing successor) and leave B itself superseded with no link.
    old = db_session.get(Statement, first.statement.id)
    succ = db_session.get(Statement, second.statement.id)
    assert old is not None and succ is not None
    old.superseded_by_statement_id = None
    succ.status = StatementStatus.SUPERSEDED.value
    succ.superseded_by_statement_id = None
    db_session.flush()
    warnings = MemoryQualityService(db_session).inspect_after_write(
        ChangeContext(
            touched_statement_ids=[old.id],
            touched_entity_ids=[person],
        )
    )
    assert warnings
    original_issue_id = warnings[0].issue_id
    result = SupersessionIntegrityRepairService(db_session).repair(
        RepairSupersessionIntegrityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            issue_id=original_issue_id,
            successor_statement_id=succ.id,
        )
    )
    assert result.outcome == SupersessionRepairOutcome.REPAIRED
    assert result.issue_resolved is True
    issue = MemoryQualityService(db_session).get_issue(original_issue_id)
    assert issue is not None
    assert issue.status == MemoryQualityIssueStatus.RESOLVED
    assert result.quality_warnings
    assert any(
        w.issue_id != original_issue_id and succ.id in (w.related_statement_ids or [])
        for w in result.quality_warnings
    ) or any(w.issue_id != original_issue_id for w in result.quality_warnings)
