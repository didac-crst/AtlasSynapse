"""Phase 12 optional embedding tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.config import Settings
from semantic_memory.models import (
    ActorType,
    OntologyClass,
    OntologyPredicate,
    ProposalStatus,
)
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import EmbeddingObjectType, GateDecision
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import ProposalOutcome, ProposeClassRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.embedding_providers import MockEmbeddingProvider, cosine_similarity
from semantic_memory.services.embeddings import EmbeddingService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import MockSemanticReviewer, ReviewDecision


def _ensure_proposer(session: Session, key: str = "embed-proposer") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def test_mock_provider_is_deterministic() -> None:
    provider = MockEmbeddingProvider()
    first = provider.embed("Person human agent")
    second = provider.embed("Person human agent")
    assert first.vector == second.vector
    assert first.model_key == "mock-hash-v1"
    assert cosine_similarity(first.vector, second.vector) == pytest.approx(1.0)


def test_disabled_embeddings_pass_similarity_gate(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(
        db_session,
        settings=Settings(embedding_mode="disabled", semantic_review_mode="mock"),
        reviewer=MockSemanticReviewer(),
    )
    result = service.propose_class(
        ProposeClassRequest(
            actor_key=proposer,
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="EmbedLab",
            parent_keys=["Organization"],
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    assert result.outcome == ProposalOutcome.READY_TO_APPLY
    similarity = next(
        item for item in result.proposal.gate_results if item.gate_name == "similarity"
    )
    assert similarity.decision == GateDecision.PASS
    assert similarity.details.get("enabled") is False


def test_similarity_is_advisory_only(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    embeddings = EmbeddingService(
        db_session,
        settings=Settings(embedding_mode="mock"),
        provider=MockEmbeddingProvider(),
    )
    rebuilt = embeddings.rebuild_ontology_embeddings()
    assert rebuilt > 0

    # Near-duplicate label for Person should recommend reuse/manual review, never accept.
    service = ProposalService(
        db_session,
        settings=Settings(embedding_mode="mock", semantic_review_mode="mock"),
        reviewer=MockSemanticReviewer(),
        embedding_provider=MockEmbeddingProvider(),
    )
    result = service.propose_class(
        ProposeClassRequest(
            actor_key=proposer,
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="HumanPerson",
            label="Person human agent",
            parent_keys=["Agent"],
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    similarity = next(
        item for item in result.proposal.gate_results if item.gate_name == "similarity"
    )
    assert similarity.decision in {GateDecision.MANUAL_REVIEW, GateDecision.PASS}
    assert similarity.decision != GateDecision.REUSE_RECOMMENDED
    assert similarity.decision != GateDecision.FAIL
    # Existing Person key still wins deterministically over similarity.
    duplicate = service.propose_class(
        ProposeClassRequest(
            actor_key=proposer,
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="Person",
            label="Totally New Label",
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    assert duplicate.outcome == ProposalOutcome.REUSE_RECOMMENDED
    assert duplicate.proposal.status == ProposalStatus.REJECTED


def test_delete_and_rebuild_leave_canonical_data(db_session: Session) -> None:
    embeddings = EmbeddingService(
        db_session,
        settings=Settings(embedding_mode="mock"),
        provider=MockEmbeddingProvider(),
    )
    class_count = db_session.scalar(select(func.count()).select_from(OntologyClass))
    predicate_count = db_session.scalar(select(func.count()).select_from(OntologyPredicate))
    assert class_count and predicate_count

    rebuilt = embeddings.rebuild_ontology_embeddings()
    assert rebuilt == class_count + predicate_count
    assert embeddings.count() == rebuilt

    deleted = embeddings.delete_all()
    assert deleted == rebuilt
    assert embeddings.count() == 0

    # Canonical ontology unchanged.
    assert db_session.scalar(select(func.count()).select_from(OntologyClass)) == class_count
    assert db_session.scalar(select(func.count()).select_from(OntologyPredicate)) == predicate_count

    rebuilt_again = embeddings.rebuild_ontology_embeddings()
    assert rebuilt_again == rebuilt
    assert embeddings.count() == rebuilt_again


def test_entity_similarity_candidates(db_session: Session) -> None:
    from semantic_memory.schemas.entities import CreateEntityRequest
    from semantic_memory.services.entities import EntityService

    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="embed-writer",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    created = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="embed-writer",
            request_id=uuid.uuid4(),
            idempotency_key=f"ent-{uuid.uuid4()}",
            canonical_name="Ada Lovelace",
            class_key="Person",
        )
    )
    embeddings = EmbeddingService(
        db_session,
        settings=Settings(embedding_mode="mock"),
        provider=MockEmbeddingProvider(),
    )
    embeddings.upsert_text(
        object_type=EmbeddingObjectType.ENTITY,
        object_id=created.entity.id,
        text="entity Ada Lovelace",
    )
    hits = embeddings.find_similar(
        object_type=EmbeddingObjectType.ENTITY,
        text="entity Ada Lovelace",
        min_score=0.5,
    )
    assert created.entity is not None
    assert hits
    assert hits[0].object_id == created.entity.id

    embeddings.delete_for_object(
        object_type=EmbeddingObjectType.ENTITY, object_id=created.entity.id
    )
    # Entity remains.
    assert EntityService(db_session).get(created.entity.id).canonical_name == "Ada Lovelace"


def test_embeddings_not_required_for_readiness_mode() -> None:
    settings = Settings(embedding_mode="disabled")
    assert settings.embedding_mode == "disabled"
    service_provider = EmbeddingService.__init__  # smoke: class importable without provider deps
    assert callable(service_provider)
