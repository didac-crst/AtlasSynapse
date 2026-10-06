"""PR6: server-owned implicit entity resolution on statement writes."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.models import ActorType, Entity, Statement
from semantic_memory.models.enums import AliasIdentityStrength, BatchStatus
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.batches import AssertBatchRequest, BatchStatementItem
from semantic_memory.schemas.entities import (
    AddEntityAliasRequest,
    CreateEntityRequest,
    EntityInput,
    ResolutionOutcome,
)
from semantic_memory.schemas.statements import AssertionOutcome, AssertStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.batches import BatchService
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


def test_authoritative_alias_reuses_existing_for_assert(db_session: Session) -> None:
    _ensure_writer(db_session)
    entities = EntityService(db_session)
    created = entities.create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Didac",
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
            alias="Didac Cristobal",
            identity_strength=AliasIdentityStrength.AUTHORITATIVE,
        )
    )
    project_id = _create(db_session, name="Atlas", class_key="Project")

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name="Didac Cristobal",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert result.outcome == AssertionOutcome.CREATE
    assert result.statement is not None
    assert result.statement.subject_entity_id == created.entity.id
    assert result.subject_identity is not None
    assert result.subject_identity.resolution.value == "MATCH"
    assert result.subject_identity.action.value == "REUSE"


def test_supporting_john_smith_collision_clarifies_without_write(db_session: Session) -> None:
    _ensure_writer(db_session)
    _create(db_session, name="John Smith", class_key="Person")
    project_id = _create(db_session, name="Collision Project", class_key="Project")
    before = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(canonical_name="John Smith", class_key="Person"),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert result.outcome == AssertionOutcome.CLARIFY
    assert result.statement is None
    assert result.subject_identity is not None
    assert result.subject_identity.resolution.value == "AMBIGUOUS"
    after = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after == before


def test_no_candidate_creates_entity_then_asserts(db_session: Session) -> None:
    _ensure_writer(db_session)
    project_id = _create(db_session, name="Fresh Project", class_key="Project")
    before_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name="Brand New Person",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object_entity_id=project_id,
        )
    )
    assert result.outcome == AssertionOutcome.CREATE
    assert result.statement is not None
    assert result.subject_identity is not None
    assert result.subject_identity.action.value == "CREATE"
    after_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    assert after_entities == before_entities + 1


def test_subject_match_object_ambiguous_persists_nothing(db_session: Session) -> None:
    _ensure_writer(db_session)
    entities = EntityService(db_session)
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Known Subject",
            class_key="Person",
        )
    )
    assert subject.entity is not None
    entities.add_entity_alias(
        AddEntityAliasRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entity_id=subject.entity.id,
            alias="Known Subject Alias",
            identity_strength=AliasIdentityStrength.AUTHORITATIVE,
        )
    )
    _create(db_session, name="Object Smith", class_key="Person")
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    before_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject=EntityInput(
                canonical_name="Known Subject Alias",
                class_key="Person",
            ),
            predicate_key="relatedTo",
            object=EntityInput(canonical_name="Object Smith", class_key="Person"),
        )
    )
    assert result.outcome == AssertionOutcome.CLARIFY
    assert result.statement is None
    assert result.subject_identity is not None
    assert result.subject_identity.resolution.value == "MATCH"
    assert result.object_identity is not None
    assert result.object_identity.resolution.value == "AMBIGUOUS"
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    after_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    assert after_statements == before_statements
    assert after_entities == before_entities


def test_batch_ambiguous_row_rolls_back_entire_batch(db_session: Session) -> None:
    _ensure_writer(db_session)
    _create(db_session, name="Batch John Smith", class_key="Person")
    project_id = _create(db_session, name="Batch Project", class_key="Project")
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0

    batch = BatchService(db_session).assert_batch(
        AssertBatchRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statements=[
                BatchStatementItem(
                    client_item_id="ok-row",
                    subject=EntityInput(
                        canonical_name="Unique Batch Person",
                        class_key="Person",
                    ),
                    predicate_key="relatedTo",
                    object_entity_id=project_id,
                ),
                BatchStatementItem(
                    client_item_id="ambiguous-row",
                    subject=EntityInput(
                        canonical_name="Batch John Smith",
                        class_key="Person",
                    ),
                    predicate_key="relatedTo",
                    object_entity_id=project_id,
                ),
            ],
        )
    )
    assert batch.status == BatchStatus.FAILED
    assert batch.created == []
    assert len(batch.ambiguous) == 1
    assert batch.ambiguous[0].client_item_id == "ambiguous-row"
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_statements == before_statements


def test_direct_uuid_input_unchanged(db_session: Session) -> None:
    _ensure_writer(db_session)
    subject_id = _create(db_session, name="UUID Subject", class_key="Person")
    object_id = _create(db_session, name="UUID Object", class_key="Project")

    result = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=subject_id,
            predicate_key="relatedTo",
            object_entity_id=object_id,
        )
    )
    assert result.outcome == AssertionOutcome.CREATE
    assert result.statement is not None
    assert result.statement.subject_entity_id == subject_id
    assert result.statement.object_entity_id == object_id
    assert result.subject_identity is not None
    assert result.subject_identity.decision_basis == "provided_entity_id"
    reused = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Unrelated",
            class_key="Person",
        )
    )
    assert reused.outcome in {ResolutionOutcome.CREATE, ResolutionOutcome.AMBIGUOUS}
