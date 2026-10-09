"""Phase A: Claim ontology + retrieval isolation.

Proves AtlasSynapse can remember “document contains hypothesis X”
without treating X as an effective world fact under default search.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.retrieval import (
    RelevantContextRequest,
    SearchSemanticMemoryRequest,
)
from semantic_memory.schemas.statements import AssertStatementRequest
from semantic_memory.seeding.bootstrap import bootstrap_system_ontology
from semantic_memory.seeding.claim_ontology import (
    CLAIM_BINDING_PREDICATE_KEYS,
    CLAIM_CLASS_KEY,
    ensure_claim_ontology,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.retrieval import RetrievalService
from semantic_memory.services.statements import StatementService


def _writer(session: Session, key: str = "claim-writer") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def _seed_hypothesis_graph(session: Session) -> dict[str, uuid.UUID]:
    """Document makesClaim Claim(hypothesis about AtlasSynapse using AGPL)."""
    bootstrap_system_ontology(session)
    writer = _writer(session)
    entities = EntityService(session)
    statements = StatementService(session)

    project = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"proj-{uuid.uuid4()}",
            canonical_name="AtlasSynapse",
            class_key="Project",
        )
    )
    document = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"doc-{uuid.uuid4()}",
            canonical_name="Strategy notes",
            class_key="Document",
        )
    )
    claim = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"claim-{uuid.uuid4()}",
            canonical_name="Hypothesis: AtlasSynapse should use AGPL",
            class_key=CLAIM_CLASS_KEY,
        )
    )
    assert project.entity is not None
    assert document.entity is not None
    assert claim.entity is not None

    def _assert(**kwargs: object) -> None:
        result = statements.assert_statement(
            AssertStatementRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"as-{uuid.uuid4()}",
                **kwargs,  # type: ignore[arg-type]
            )
        )
        assert result.statement is not None

    _assert(
        subject_entity_id=document.entity.id,
        predicate_key="makesClaim",
        object_entity_id=claim.entity.id,
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="claimText",
        object_string="AtlasSynapse should use AGPL",
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="claimSubject",
        object_entity_id=project.entity.id,
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="claimPredicateKey",
        object_string="shouldUseLicense",
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="claimObjectString",
        object_string="AGPL",
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="epistemicKind",
        object_string="hypothesis",
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="claimPolarity",
        object_string="positive",
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="claimStatus",
        object_string="active",
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="claimDerivation",
        object_string="explicit",
    )
    _assert(
        subject_entity_id=claim.entity.id,
        predicate_key="aboutEntity",
        object_entity_id=project.entity.id,
    )
    # Ordinary world fact — must remain visible under default search.
    _assert(
        subject_entity_id=project.entity.id,
        predicate_key="description",
        object_string="Governed semantic memory for agents",
    )

    return {
        "project_id": project.entity.id,
        "document_id": document.entity.id,
        "claim_id": claim.entity.id,
    }


def test_ensure_claim_ontology_idempotent(db_session: Session) -> None:
    bootstrap_system_ontology(db_session)
    first = ensure_claim_ontology(db_session)
    second = ensure_claim_ontology(db_session)
    assert first["created_classes"] == 0
    assert first["created_predicates"] == 0
    assert second["created_classes"] == 0
    assert second["created_predicates"] == 0
    assert CLAIM_CLASS_KEY
    assert "makesClaim" in CLAIM_BINDING_PREDICATE_KEYS


def test_hypothesis_does_not_leak_into_default_search(db_session: Session) -> None:
    ids = _seed_hypothesis_graph(db_session)
    retrieval = RetrievalService(db_session)

    default = retrieval.search_semantic_memory(
        SearchSemanticMemoryRequest(query="AtlasSynapse should use AGPL", limit=25)
    )
    entity_ids = {hit.entity.id for hit in default.hits if hit.entity is not None}
    statement_preds = {
        hit.statement.predicate_key for hit in default.hits if hit.statement is not None
    }
    statement_subjects = {
        hit.statement.subject_entity_id for hit in default.hits if hit.statement is not None
    }
    statement_objects = {
        hit.statement.object_entity_id
        for hit in default.hits
        if hit.statement is not None and hit.statement.object_entity_id is not None
    }

    assert ids["claim_id"] not in entity_ids
    assert ids["claim_id"] not in statement_subjects
    assert ids["claim_id"] not in statement_objects
    assert statement_preds.isdisjoint(CLAIM_BINDING_PREDICATE_KEYS)
    # Lexical hit on claimText must not surface the Claim or its bindings.
    for hit in default.hits:
        if hit.statement is not None:
            assert hit.statement.object_string != "AtlasSynapse should use AGPL"
            assert hit.statement.object_string != "shouldUseLicense"


def test_include_claims_returns_epistemic_graph(db_session: Session) -> None:
    ids = _seed_hypothesis_graph(db_session)
    retrieval = RetrievalService(db_session)

    opted = retrieval.search_semantic_memory(
        SearchSemanticMemoryRequest(
            query="AtlasSynapse should use AGPL",
            include_claims=True,
            limit=25,
        )
    )
    entity_ids = {hit.entity.id for hit in opted.hits if hit.entity is not None}
    statement_preds = {
        hit.statement.predicate_key for hit in opted.hits if hit.statement is not None
    }
    assert ids["claim_id"] in entity_ids or "claimText" in statement_preds
    assert statement_preds & CLAIM_BINDING_PREDICATE_KEYS


def test_ordinary_fact_still_visible_by_default(db_session: Session) -> None:
    ids = _seed_hypothesis_graph(db_session)
    retrieval = RetrievalService(db_session)

    result = retrieval.search_semantic_memory(
        SearchSemanticMemoryRequest(query="Governed semantic memory for agents", limit=25)
    )
    descriptions = [
        hit.statement.object_string
        for hit in result.hits
        if hit.statement is not None and hit.statement.predicate_key == "description"
    ]
    assert "Governed semantic memory for agents" in descriptions
    assert ids["project_id"] in {
        hit.statement.subject_entity_id for hit in result.hits if hit.statement is not None
    }


def test_document_context_excludes_claims_by_default(db_session: Session) -> None:
    ids = _seed_hypothesis_graph(db_session)
    retrieval = RetrievalService(db_session)

    ctx = retrieval.get_relevant_context(
        RelevantContextRequest(entity_id=ids["document_id"], limit=50)
    )
    preds = {ranked.statement.predicate_key for ranked in ctx.statements}
    timeline_preds = (
        {entry.statement.predicate_key for entry in ctx.timeline.entries}
        if ctx.timeline is not None
        else set()
    )
    neighbor_preds = (
        {edge.statement.predicate_key for edge in ctx.neighborhood.edges}
        if ctx.neighborhood is not None
        else set()
    )
    assert preds.isdisjoint(CLAIM_BINDING_PREDICATE_KEYS)
    assert timeline_preds.isdisjoint(CLAIM_BINDING_PREDICATE_KEYS)
    assert neighbor_preds.isdisjoint(CLAIM_BINDING_PREDICATE_KEYS)
    if ctx.neighborhood is not None:
        assert ids["claim_id"] not in {
            edge.neighbor_entity_id
            for edge in ctx.neighborhood.edges
            if edge.neighbor_entity_id is not None
        }


def test_direct_claim_lookup_exposes_structure(db_session: Session) -> None:
    ids = _seed_hypothesis_graph(db_session)
    retrieval = RetrievalService(db_session)

    ctx = retrieval.get_relevant_context(
        RelevantContextRequest(entity_id=ids["claim_id"], limit=50)
    )
    assert ctx.entity is not None
    assert any(t.class_key == CLAIM_CLASS_KEY for t in ctx.entity.types)
    preds = {ranked.statement.predicate_key for ranked in ctx.statements}
    assert "claimText" in preds or (
        ctx.timeline is not None
        and any(e.statement.predicate_key == "claimText" for e in ctx.timeline.entries)
    )
    assert ctx.metadata.get("focus_is_claim") is True


def test_project_context_does_not_treat_hypothesis_as_fact(db_session: Session) -> None:
    """aboutEntity / claimSubject must not appear as ordinary facts about the project."""
    ids = _seed_hypothesis_graph(db_session)
    retrieval = RetrievalService(db_session)

    ctx = retrieval.get_relevant_context(
        RelevantContextRequest(entity_id=ids["project_id"], limit=50)
    )
    for ranked in ctx.statements:
        assert ranked.statement.predicate_key not in CLAIM_BINDING_PREDICATE_KEYS
        assert ranked.statement.object_string != "AGPL"
        assert ranked.statement.object_string != "shouldUseLicense"
    if ctx.timeline is not None:
        for entry in ctx.timeline.entries:
            assert entry.statement.predicate_key not in CLAIM_BINDING_PREDICATE_KEYS
