"""Write/identity clarification continuation handles."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.exceptions import ClarificationRequestAlreadyResolvedError
from semantic_memory.models import ActorType, Entity, Statement
from semantic_memory.models.enums import (
    EntityStatus,
    WriteClarificationResolution,
    WriteClarificationStatus,
)
from semantic_memory.models.operations import WriteClarificationRequest
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.dry_run import OperationMode
from semantic_memory.schemas.entities import CreateEntityRequest, EntityInput
from semantic_memory.schemas.statements import AssertionOutcome, AssertStatementRequest
from semantic_memory.schemas.write_clarifications import AnswerIdentityClarificationRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.statements import StatementService
from semantic_memory.services.write_clarifications import WriteClarificationService


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def _create(session: Session, *, name: str, class_key: str = "Person") -> uuid.UUID:
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


def test_clarify_issues_durable_handle_on_dry_run(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    _create(db_session, name=f"ZZClarPerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZClarProject-{suffix}", class_key="Project")
    before_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            dry_run=True,
            subject=EntityInput(
                canonical_name=f"ZZClarPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert result.outcome == AssertionOutcome.CLARIFY
    assert result.clarification_request_id is not None
    assert result.operation_mode == OperationMode.DRY_RUN
    assert result.would_persist is False

    row = db_session.get(WriteClarificationRequest, result.clarification_request_id)
    assert row is not None
    assert row.status == WriteClarificationStatus.OPEN.value
    assert row.operation_mode == OperationMode.DRY_RUN.value
    assert row.ambiguous_path == "subject"
    assert row.expires_at > datetime.now(UTC)

    after_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_entities == before_entities
    assert after_statements == before_statements


def test_answer_chosen_entity_resumes_and_persists(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    person_id = _create(db_session, name=f"ZZPickPerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZPickProject-{suffix}", class_key="Project")

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZPickPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.outcome == AssertionOutcome.CLARIFY
    assert clarified.clarification_request_id is not None
    assert clarified.subject_identity is not None
    candidate_ids = [item.entity_id for item in clarified.subject_identity.candidates]
    assert person_id in candidate_ids

    answered = WriteClarificationService(db_session).answer(
        AnswerIdentityClarificationRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=clarified.clarification_request_id,
            resolution=WriteClarificationResolution.CHOSEN_ENTITY,
            chosen_entity_id=person_id,
        )
    )
    assert answered.status == WriteClarificationStatus.RESOLVED
    assert answered.assert_result is not None
    assert answered.assert_result.outcome == AssertionOutcome.CREATE
    assert answered.assert_result.statement is not None
    assert answered.assert_result.statement.subject_entity_id == person_id

    row = db_session.get(WriteClarificationRequest, clarified.clarification_request_id)
    assert row is not None
    assert row.status == WriteClarificationStatus.RESOLVED.value
    assert row.resolution == WriteClarificationResolution.CHOSEN_ENTITY.value


def test_answer_reject_persists_nothing(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    _create(db_session, name=f"ZZRejectPerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZRejectProject-{suffix}", class_key="Project")
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZRejectPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.clarification_request_id is not None

    answered = WriteClarificationService(db_session).answer(
        AnswerIdentityClarificationRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=clarified.clarification_request_id,
            resolution=WriteClarificationResolution.REJECT,
        )
    )
    assert answered.status == WriteClarificationStatus.RESOLVED
    assert answered.assert_result is None
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_statements == before_statements


def test_answer_create_new_forces_entity(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    _create(db_session, name=f"ZZForcePerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZForceProject-{suffix}", class_key="Project")
    before_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZForcePerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.clarification_request_id is not None

    answered = WriteClarificationService(db_session).answer(
        AnswerIdentityClarificationRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=clarified.clarification_request_id,
            resolution=WriteClarificationResolution.CREATE_NEW,
        )
    )
    assert answered.status == WriteClarificationStatus.RESOLVED
    assert answered.assert_result is not None
    assert answered.assert_result.outcome == AssertionOutcome.CREATE
    assert answered.assert_result.statement is not None
    assert answered.assert_result.statement.subject_entity_id != project_id
    after_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    assert after_entities == before_entities + 1


def test_stale_chosen_entity_reruns_against_prod(db_session: Session) -> None:
    """Inactive chosen candidate must not be trusted; re-check current prod."""
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    person_id = _create(db_session, name=f"ZZStalePerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZStaleProject-{suffix}", class_key="Project")

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZStalePerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.clarification_request_id is not None

    entity = db_session.get(Entity, person_id)
    assert entity is not None
    entity.status = EntityStatus.DEPRECATED.value
    db_session.flush()

    answered = WriteClarificationService(db_session).answer(
        AnswerIdentityClarificationRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=clarified.clarification_request_id,
            resolution=WriteClarificationResolution.CHOSEN_ENTITY,
            chosen_entity_id=person_id,
        )
    )
    # Deprecated candidate excluded → re-run may CREATE (resolved) or re-clarify.
    assert answered.status in {
        WriteClarificationStatus.RESOLVED,
        WriteClarificationStatus.SUPERSEDED,
    }
    if answered.status == WriteClarificationStatus.SUPERSEDED:
        assert answered.open_clarification is not None
        assert (
            answered.open_clarification.clarification_request_id
            != clarified.clarification_request_id
        )
    else:
        assert answered.assert_result is not None
        assert answered.assert_result.outcome == AssertionOutcome.CREATE
        assert answered.assert_result.statement is not None
        assert answered.assert_result.statement.subject_entity_id != person_id


def test_expired_clarification_rejects_answer(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    _create(db_session, name=f"ZZExpPerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZExpProject-{suffix}", class_key="Project")

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZExpPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.clarification_request_id is not None
    row = db_session.get(WriteClarificationRequest, clarified.clarification_request_id)
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()

    with pytest.raises(ClarificationRequestAlreadyResolvedError) as exc_info:
        WriteClarificationService(db_session).answer(
            AnswerIdentityClarificationRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                clarification_request_id=clarified.clarification_request_id,
                resolution=WriteClarificationResolution.REJECT,
            )
        )
    assert "expired" in exc_info.value.message.lower()
    row = db_session.get(WriteClarificationRequest, clarified.clarification_request_id)
    assert row is not None
    assert row.status == WriteClarificationStatus.EXPIRED.value


def test_dry_run_clarification_answer_never_persists(db_session: Session) -> None:
    """Answering a dry-run clarification must remain dry-run (preview ≠ write)."""
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    person_id = _create(db_session, name=f"ZZDryAnsPerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZDryAnsProject-{suffix}", class_key="Project")
    before_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            dry_run=True,
            subject=EntityInput(
                canonical_name=f"ZZDryAnsPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.clarification_request_id is not None
    row = db_session.get(WriteClarificationRequest, clarified.clarification_request_id)
    assert row is not None
    assert row.operation_mode == OperationMode.DRY_RUN.value
    # Even if frozen payload were tampered to dry_run=false, resume must stay dry-run.
    row.frozen_request = {**row.frozen_request, "dry_run": False}
    db_session.flush()

    answered = WriteClarificationService(db_session).answer(
        AnswerIdentityClarificationRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=clarified.clarification_request_id,
            resolution=WriteClarificationResolution.CHOSEN_ENTITY,
            chosen_entity_id=person_id,
        )
    )
    assert answered.status == WriteClarificationStatus.RESOLVED
    assert answered.assert_result is not None
    assert answered.assert_result.dry_run is True
    assert answered.assert_result.operation_mode == OperationMode.DRY_RUN
    assert answered.assert_result.would_persist is True
    assert answered.assert_result.outcome == AssertionOutcome.CREATE

    after_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_entities == before_entities
    assert after_statements == before_statements


def test_clarification_id_is_one_shot_after_resolve(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    person_id = _create(db_session, name=f"ZZOncePerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZOnceProject-{suffix}", class_key="Project")

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZOncePerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.clarification_request_id is not None
    clar_id = clarified.clarification_request_id

    WriteClarificationService(db_session).answer(
        AnswerIdentityClarificationRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=clar_id,
            resolution=WriteClarificationResolution.CHOSEN_ENTITY,
            chosen_entity_id=person_id,
        )
    )

    with pytest.raises(ClarificationRequestAlreadyResolvedError):
        WriteClarificationService(db_session).answer(
            AnswerIdentityClarificationRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                clarification_request_id=clar_id,
                resolution=WriteClarificationResolution.REJECT,
            )
        )


def test_clarification_id_is_one_shot_after_reject(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    _create(db_session, name=f"ZZOnceRejPerson-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZOnceRejProject-{suffix}", class_key="Project")

    clarified = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZOnceRejPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert clarified.clarification_request_id is not None
    clar_id = clarified.clarification_request_id

    WriteClarificationService(db_session).answer(
        AnswerIdentityClarificationRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=clar_id,
            resolution=WriteClarificationResolution.REJECT,
        )
    )

    with pytest.raises(ClarificationRequestAlreadyResolvedError):
        WriteClarificationService(db_session).answer(
            AnswerIdentityClarificationRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                clarification_request_id=clar_id,
                resolution=WriteClarificationResolution.CREATE_NEW,
            )
        )
