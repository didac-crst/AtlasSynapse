"""Atomic assert_batch ingestion tests."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.models import ActorType, Entity, IngestionBatch, Statement
from semantic_memory.models.enums import BatchStatus
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.batches import AssertBatchRequest, BatchEntityItem, BatchStatementItem
from semantic_memory.services.actors import ActorService
from semantic_memory.services.batches import BatchService


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def test_assert_batch_creates_entity_and_statement(db_session: Session) -> None:
    _ensure_writer(db_session)
    result = BatchService(db_session).assert_batch(
        AssertBatchRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entities=[
                BatchEntityItem(
                    client_item_id="doc-1",
                    canonical_name="Batch Doc",
                    class_key="Document",
                )
            ],
            statements=[
                BatchStatementItem(
                    client_item_id="stmt-1",
                    subject_client_item_id="doc-1",
                    predicate_key="description",
                    object_string="from batch",
                )
            ],
        )
    )
    assert result.status == BatchStatus.SUCCEEDED
    assert len(result.created) == 2
    assert result.rejected == []
    assert result.ontology_required == []
    entity_result = next(item for item in result.created if item.entity is not None)
    assert entity_result.entity is not None
    assert entity_result.entity.entity is not None
    created_entity_id = entity_result.entity.entity.id
    entity_count = db_session.scalar(
        select(func.count()).select_from(Entity).where(Entity.id == created_entity_id)
    )
    assert entity_count == 1
    statement_count = db_session.scalar(
        select(func.count())
        .select_from(Statement)
        .where(Statement.subject_entity_id == created_entity_id)
    )
    assert statement_count == 1
    batch = db_session.get(IngestionBatch, result.batch_id)
    assert batch is not None
    assert batch.status == BatchStatus.SUCCEEDED.value


def test_assert_batch_atomic_rollback_on_ontology_required(db_session: Session) -> None:
    _ensure_writer(db_session)
    before_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    before_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    result = BatchService(db_session).assert_batch(
        AssertBatchRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entities=[
                BatchEntityItem(
                    client_item_id="doc-1",
                    canonical_name="Should Roll Back",
                    class_key="Document",
                )
            ],
            statements=[
                BatchStatementItem(
                    client_item_id="stmt-1",
                    subject_client_item_id="doc-1",
                    predicate_key="notARealPredicate",
                    object_string="nope",
                )
            ],
        )
    )
    assert result.status == BatchStatus.FAILED
    assert result.created == []
    assert len(result.ontology_required) == 1
    assert result.ontology_required[0].error_code == "UNKNOWN_PREDICATE"
    after_entities = db_session.scalar(select(func.count()).select_from(Entity)) or 0
    after_statements = db_session.scalar(select(func.count()).select_from(Statement)) or 0
    assert after_entities == before_entities
    assert after_statements == before_statements
    batch = db_session.get(IngestionBatch, result.batch_id)
    assert batch is not None
    assert batch.status == BatchStatus.FAILED.value


def test_assert_batch_idempotent_retry(db_session: Session) -> None:
    _ensure_writer(db_session)
    request = AssertBatchRequest(
        actor_key="writer",
        request_id=uuid.uuid4(),
        idempotency_key="batch-idem-1",
        entities=[
            BatchEntityItem(
                client_item_id="doc-1",
                canonical_name="Idem Doc",
                class_key="Document",
            )
        ],
        statements=[
            BatchStatementItem(
                client_item_id="stmt-1",
                subject_client_item_id="doc-1",
                predicate_key="name",
                object_string="Stable",
            )
        ],
    )
    first = BatchService(db_session).assert_batch(request)
    second = BatchService(db_session).assert_batch(request)
    assert first.status == BatchStatus.SUCCEEDED
    assert second.status == BatchStatus.SUCCEEDED
    assert first.batch_id == second.batch_id
    entity_count = db_session.scalar(
        select(func.count()).select_from(Entity).where(Entity.canonical_name == "Idem Doc")
    )
    assert entity_count == 1
