"""Dry-run mutations: full decision path, no durable knowledge writes."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.models import ActorType, Entity, Statement
from semantic_memory.models.enums import AliasIdentityStrength, OperationStatus
from semantic_memory.models.operations import OperationLog
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.dry_run import (
    EntityWriteAction,
    OperationMode,
    StatementWriteAction,
)
from semantic_memory.schemas.entities import (
    AddEntityAliasRequest,
    CreateEntityRequest,
    EntityInput,
    ResolutionOutcome,
)
from semantic_memory.schemas.statements import AssertionOutcome, AssertStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.statements import StatementService


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


def test_assert_dry_run_create_persists_nothing(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    project_id = _create(db_session, name=f"ZZDryProject-{suffix}", class_key="Project")
    before_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            dry_run=True,
            subject=EntityInput(
                canonical_name=f"ZZDryPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert result.dry_run is True
    assert result.operation_mode == OperationMode.DRY_RUN
    assert result.would_persist is True
    assert result.outcome == AssertionOutcome.CREATE
    assert result.statement_action == StatementWriteAction.WOULD_CREATE
    assert result.statement is not None
    assert result.subject_identity is not None
    assert result.subject_identity.action.value == "CREATE"

    after_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_entities == before_entities
    assert after_statements == before_statements


def test_assert_dry_run_ambiguous_matches_live_clarify(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    _create(db_session, name=f"ZZDryAmbiguous-{suffix}", class_key="Person")
    project_id = _create(db_session, name=f"ZZDryAmbProject-{suffix}", class_key="Project")
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    live = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name=f"ZZDryAmbiguous-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    preview = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            dry_run=True,
            subject=EntityInput(
                canonical_name=f"ZZDryAmbiguous-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert live.outcome == AssertionOutcome.CLARIFY
    assert preview.outcome == AssertionOutcome.CLARIFY
    assert preview.would_persist is False
    assert preview.statement_action == StatementWriteAction.NOT_WRITTEN
    assert preview.subject_identity is not None
    assert preview.subject_identity.resolution.value == "AMBIGUOUS"
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_statements == before_statements


def test_assert_dry_run_reuses_authoritative_alias(db_session: Session) -> None:
    _ensure_writer(db_session)
    entities = EntityService(db_session)
    suffix = uuid.uuid4().hex[:8]
    canonical = f"ZZDryReuse-{suffix}"
    alias = f"ZZDryReuseAlias-{suffix}"
    created = entities.create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=canonical,
            class_key="Person",
        )
    )
    assert created.entity is not None
    entities.add_entity_alias(
        AddEntityAliasRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entity_id=created.entity.id,
            alias=alias,
            identity_strength=AliasIdentityStrength.AUTHORITATIVE,
        )
    )
    project_id = _create(db_session, name=f"ZZDryReuseProject-{suffix}", class_key="Project")
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            dry_run=True,
            subject=EntityInput(canonical_name=alias, class_key="Person"),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert result.would_persist is True
    assert result.statement_action == StatementWriteAction.WOULD_CREATE
    assert result.subject_identity is not None
    assert result.subject_identity.resolution.value == "MATCH"
    assert result.subject_identity.entity_id == created.entity.id
    assert result.statement is not None
    assert result.statement.subject_entity_id == created.entity.id
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_statements == before_statements


def test_create_entity_dry_run_persists_nothing(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    before = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    result = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            dry_run=True,
            canonical_name=f"ZZDryCreateEntity-{suffix}",
            class_key="Person",
        )
    )
    assert result.dry_run is True
    assert result.operation_mode == OperationMode.DRY_RUN
    assert result.would_persist is True
    assert result.outcome == ResolutionOutcome.CREATE
    assert result.entity_action == EntityWriteAction.WOULD_CREATE
    assert result.entity is not None
    after = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    assert after == before


def test_dry_run_records_operation_log_only(db_session: Session) -> None:
    _ensure_writer(db_session)
    suffix = uuid.uuid4().hex[:8]
    project_id = _create(db_session, name=f"ZZDryLogProject-{suffix}", class_key="Project")
    before_ops = db_session.scalar(select(func.count()).select_from(OperationLog)) or 0
    request_id = uuid.uuid4()

    StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=request_id,
            idempotency_key=str(uuid.uuid4()),
            dry_run=True,
            subject=EntityInput(
                canonical_name=f"ZZDryLogPerson-{suffix}",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    after_ops = db_session.scalar(select(func.count()).select_from(OperationLog)) or 0
    assert after_ops == before_ops + 1
    op = db_session.scalar(select(OperationLog).where(OperationLog.request_id == request_id))
    assert op is not None
    assert op.status == OperationStatus.SUCCESS.value
    assert op.operation_name == "assert_statement"
    assert op.request_payload is not None
    assert op.request_payload.get("dry_run") is True
    assert op.response_payload is not None
    assert op.response_payload.get("operation_mode") == OperationMode.DRY_RUN.value
    assert op.response_payload.get("would_persist") is True
