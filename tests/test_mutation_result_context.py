"""Mutation result projections: effective_state, return_mode, actionable CLARIFY."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.statements import (
    AssertStatementRequest,
    CorrectStatementRequest,
    RetractStatementRequest,
    StatementReturnMode,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.statements import StatementService


def _writer(session: Session, key: str = "mut-ctx-writer") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def test_assert_standard_returns_effective_state_without_readback(db_session: Session) -> None:
    writer = _writer(db_session)
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    suffix = uuid.uuid4().hex[:8]
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"mc-s-{uuid.uuid4()}",
            canonical_name=f"MutCtx Subject {suffix}",
            class_key="Document",
        )
    )
    peer = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"mc-o-{uuid.uuid4()}",
            canonical_name=f"MutCtx Peer {suffix}",
            class_key="Document",
        )
    )
    assert subject.entity is not None and peer.entity is not None
    result = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"mc-a-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer.entity.id,
            return_mode=StatementReturnMode.STANDARD,
        )
    )
    assert result.outcome.value == "CREATE"
    assert result.statement is not None
    assert result.effective_state is not None
    assert result.effective_state.predicate_key == "relatedTo"
    assert result.effective_state.subject_entity_id == subject.entity.id
    assert len(result.effective_state.values) == 1
    assert result.effective_state.values[0].entity_id == peer.entity.id
    assert result.effective_state.values[0].canonical_name == peer.entity.canonical_name
    assert result.changes is not None
    assert result.changes.created_statement_id == result.statement.id
    assert result.context is None  # standard omits contextual slice
    assert result.subject_identity is not None


def test_assert_minimal_omits_statement_body(db_session: Session) -> None:
    writer = _writer(db_session, key="mut-min-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    suffix = uuid.uuid4().hex[:8]
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"mm-s-{uuid.uuid4()}",
            canonical_name=f"Min Subject {suffix}",
            class_key="Document",
        )
    )
    peer = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"mm-o-{uuid.uuid4()}",
            canonical_name=f"Min Peer {suffix}",
            class_key="Document",
        )
    )
    assert subject.entity is not None and peer.entity is not None
    result = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"mm-a-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer.entity.id,
            return_mode=StatementReturnMode.MINIMAL,
        )
    )
    assert result.statement is None
    assert result.effective_state is None
    assert result.changes is not None
    assert result.changes.created_statement_id is not None


def test_assert_contextual_bounded_one_hop(db_session: Session) -> None:
    writer = _writer(db_session, key="mut-ctxual-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    suffix = uuid.uuid4().hex[:8]
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"cx-s-{uuid.uuid4()}",
            canonical_name=f"Ctx Subject {suffix}",
            class_key="Document",
        )
    )
    assert subject.entity is not None
    # Seed several related facts, then assert one more with contextual mode.
    for i in range(4):
        peer = entities.create_entity(
            CreateEntityRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"cx-o-{i}-{uuid.uuid4()}",
                canonical_name=f"Ctx Peer {i} {suffix}",
                class_key="Document",
            )
        )
        assert peer.entity is not None
        statements.assert_statement(
            AssertStatementRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"cx-a-{i}-{uuid.uuid4()}",
                subject_entity_id=subject.entity.id,
                predicate_key="relatedTo",
                object_entity_id=peer.entity.id,
            )
        )
    final_peer = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"cx-of-{uuid.uuid4()}",
            canonical_name=f"Ctx Final {suffix}",
            class_key="Document",
        )
    )
    assert final_peer.entity is not None
    result = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"cx-af-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=final_peer.entity.id,
            return_mode=StatementReturnMode.CONTEXTUAL,
        )
    )
    assert result.context is not None
    assert result.context.related_facts_limit == 5
    assert len(result.context.related_facts) <= 5
    assert result.effective_state is not None
    assert len(result.effective_state.values) == 5  # cardinality many relatedTo


def test_correct_returns_previous_and_effective_state(db_session: Session) -> None:
    writer = _writer(db_session, key="mut-corr-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    suffix = uuid.uuid4().hex[:8]
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"cr-s-{uuid.uuid4()}",
            canonical_name=f"Corr Subject {suffix}",
            class_key="Document",
        )
    )
    peer = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"cr-o-{uuid.uuid4()}",
            canonical_name=f"Corr Peer {suffix}",
            class_key="Document",
        )
    )
    assert subject.entity is not None and peer.entity is not None
    original = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"cr-a-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer.entity.id,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert original.statement is not None
    corrected = statements.correct_statement(
        CorrectStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"cr-c-{uuid.uuid4()}",
            statement_id=original.statement.id,
            valid_from=datetime(2025, 9, 1, tzinfo=UTC),
        )
    )
    assert corrected.outcome.value == "SUPERSEDE"
    assert corrected.previous_statement.id == original.statement.id
    assert corrected.statement.valid_from == datetime(2025, 9, 1, tzinfo=UTC)
    assert corrected.subject_identity is not None
    assert corrected.effective_state is not None
    assert corrected.changes is not None
    assert original.statement.id in corrected.changes.superseded_statement_ids


def test_retract_updates_effective_state(db_session: Session) -> None:
    writer = _writer(db_session, key="mut-ret-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    suffix = uuid.uuid4().hex[:8]
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"rt-s-{uuid.uuid4()}",
            canonical_name=f"Ret Subject {suffix}",
            class_key="Document",
        )
    )
    peer = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"rt-o-{uuid.uuid4()}",
            canonical_name=f"Ret Peer {suffix}",
            class_key="Document",
        )
    )
    assert subject.entity is not None and peer.entity is not None
    asserted = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"rt-a-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer.entity.id,
        )
    )
    assert asserted.statement is not None
    retracted = statements.retract_statement(
        RetractStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"rt-r-{uuid.uuid4()}",
            statement_id=asserted.statement.id,
            reason="test",
        )
    )
    assert retracted.outcome.value == "RETRACT"
    assert retracted.effective_state is not None
    assert retracted.effective_state.values == []
    assert retracted.changes is not None
    assert asserted.statement.id in retracted.changes.retracted_statement_ids


def test_return_mode_not_on_generic_mutation_envelope() -> None:
    from semantic_memory.schemas.common import MutationEnvelope

    assert "return_mode" not in MutationEnvelope.model_fields
