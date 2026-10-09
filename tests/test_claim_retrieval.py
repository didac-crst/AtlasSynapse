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


def test_claim_crowd_out_does_not_hide_world_fact(db_session: Session) -> None:
    """More Claim matches than the limit must not crowd out a matching world fact."""
    bootstrap_system_ontology(db_session)
    writer = _writer(db_session, key="crowd-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    shared = "UniqueCrowdTokenXYZ42"

    project = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"crowd-proj-{uuid.uuid4()}",
            canonical_name=f"Project {shared}",
            class_key="Project",
        )
    )
    document = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"crowd-doc-{uuid.uuid4()}",
            canonical_name=f"Doc {shared}",
            class_key="Document",
        )
    )
    assert project.entity is not None and document.entity is not None

    statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"crowd-desc-{uuid.uuid4()}",
            subject_entity_id=project.entity.id,
            predicate_key="description",
            object_string=f"World fact about {shared}",
        )
    )

    for i in range(12):
        claim = entities.create_entity(
            CreateEntityRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"crowd-claim-{i}-{uuid.uuid4()}",
                canonical_name=f"Claim {i} {shared}",
                class_key=CLAIM_CLASS_KEY,
            )
        )
        assert claim.entity is not None
        statements.assert_statement(
            AssertStatementRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"crowd-text-{i}-{uuid.uuid4()}",
                subject_entity_id=claim.entity.id,
                predicate_key="claimText",
                object_string=f"Hypothesis mentioning {shared} number {i}",
            )
        )
        statements.assert_statement(
            AssertStatementRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"crowd-makes-{i}-{uuid.uuid4()}",
                subject_entity_id=document.entity.id,
                predicate_key="makesClaim",
                object_entity_id=claim.entity.id,
            )
        )

    retrieval = RetrievalService(db_session)
    result = retrieval.search_semantic_memory(SearchSemanticMemoryRequest(query=shared, limit=5))
    descriptions = [
        hit.statement.object_string
        for hit in result.hits
        if hit.statement is not None and hit.statement.predicate_key == "description"
    ]
    assert f"World fact about {shared}" in descriptions
    claim_entity_hits = [
        hit
        for hit in result.hits
        if hit.entity is not None and any(t.class_key == CLAIM_CLASS_KEY for t in hit.entity.types)
    ]
    assert claim_entity_hits == []


def test_class_key_claim_without_include_claims_does_not_broaden(
    db_session: Session,
) -> None:
    ids = _seed_hypothesis_graph(db_session)
    retrieval = RetrievalService(db_session)

    blocked = retrieval.search_semantic_memory(
        SearchSemanticMemoryRequest(
            query="AtlasSynapse",
            class_key=CLAIM_CLASS_KEY,
            include_claims=False,
            limit=25,
        )
    )
    # Zero Claim entity results; must not broaden to Project/Document entities.
    assert all(hit.entity is None for hit in blocked.hits)
    assert all(
        hit.statement is None or hit.statement.predicate_key not in CLAIM_BINDING_PREDICATE_KEYS
        for hit in blocked.hits
    )

    opted = retrieval.search_semantic_memory(
        SearchSemanticMemoryRequest(
            query="AtlasSynapse should use AGPL",
            class_key=CLAIM_CLASS_KEY,
            include_claims=True,
            limit=25,
        )
    )
    claim_hits = [
        hit
        for hit in opted.hits
        if hit.entity is not None and any(t.class_key == CLAIM_CLASS_KEY for t in hit.entity.types)
    ]
    assert any(
        hit.entity is not None and hit.entity.id == ids["claim_id"] for hit in claim_hits
    ) or (
        "claimText"
        in {hit.statement.predicate_key for hit in opted.hits if hit.statement is not None}
    )


def test_compatible_existing_claim_thing_parent_reused(db_session: Session) -> None:
    """Pre-existing Claim→Thing is reused idempotently (no conflict)."""
    bootstrap_system_ontology(db_session)
    first = ensure_claim_ontology(db_session)
    second = ensure_claim_ontology(db_session)
    assert first["created_classes"] == 0
    assert first["created_parents"] == 0
    assert second == first


def test_preexisting_claim_without_thing_parent_fails_closed(db_session: Session) -> None:
    from sqlalchemy import delete, select

    from semantic_memory.exceptions import OntologySeedConflictError
    from semantic_memory.models import OntologyClass, OntologyClassParent, OntologyNamespace
    from semantic_memory.seeding.ontology import CORE_NAMESPACE_KEY, seed_core_ontology

    seed_core_ontology(db_session)
    # Migration may already have created Claim→Thing; strip the parent to simulate
    # a foreign pre-existing Claim without Thing inheritance.
    namespace = db_session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    assert namespace is not None
    claim = db_session.scalar(
        select(OntologyClass).where(
            OntologyClass.namespace_id == namespace.id,
            OntologyClass.key == CLAIM_CLASS_KEY,
        )
    )
    thing = db_session.scalar(
        select(OntologyClass).where(
            OntologyClass.namespace_id == namespace.id,
            OntologyClass.key == "Thing",
        )
    )
    assert claim is not None and thing is not None
    db_session.execute(
        delete(OntologyClassParent).where(
            OntologyClassParent.child_class_id == claim.id,
            OntologyClassParent.parent_class_id == thing.id,
        )
    )
    db_session.flush()

    try:
        ensure_claim_ontology(db_session)
        raise AssertionError("expected OntologySeedConflictError")
    except OntologySeedConflictError as exc:
        assert "Thing" in str(exc)
        assert exc.details.get("class_key") == CLAIM_CLASS_KEY


def test_incompatible_makes_claim_collision_fails_closed(db_session: Session) -> None:
    from sqlalchemy import select

    from semantic_memory.exceptions import OntologySeedConflictError
    from semantic_memory.models import (
        Actor,
        OntologyClass,
        OntologyNamespace,
        OntologyPredicate,
        OntologyPredicateDomain,
        OntologyPredicateRevision,
        ValueKind,
    )
    from semantic_memory.seeding.ontology import (
        CORE_NAMESPACE_KEY,
        seed_core_ontology,
        stable_seed_id,
    )

    seed_core_ontology(db_session)
    # Migration may already have Claim ontology; remove makesClaim only if we can
    # plant an incompatible stand-in. Prefer creating a fresh incompatible key path
    # by deleting Claim predicates created by migration within this savepoint session.
    namespace = db_session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    assert namespace is not None
    actor = db_session.scalar(select(Actor).where(Actor.name == "system"))
    assert actor is not None

    existing = db_session.scalar(
        select(OntologyPredicate).where(
            OntologyPredicate.namespace_id == namespace.id,
            OntologyPredicate.key == "makesClaim",
        )
    )
    if existing is not None and existing.current_revision_id is not None:
        # Overwrite current revision semantics in-place to simulate a foreign key.
        rev = db_session.get(OntologyPredicateRevision, existing.current_revision_id)
        assert rev is not None
        rev.value_kind = ValueKind.STRING.value
        rev.cardinality = "one"
        rev.datatype = "xsd:string"
        db_session.flush()
    else:
        document = db_session.scalar(
            select(OntologyClass).where(
                OntologyClass.namespace_id == namespace.id,
                OntologyClass.key == "Document",
            )
        )
        assert document is not None
        pred = OntologyPredicate(
            id=stable_seed_id("predicate", CORE_NAMESPACE_KEY, "makesClaim"),
            namespace_id=namespace.id,
            key="makesClaim",
        )
        db_session.add(pred)
        db_session.flush()
        rev = OntologyPredicateRevision(
            id=stable_seed_id("predicate_revision", CORE_NAMESPACE_KEY, "makesClaim", "1"),
            predicate_id=pred.id,
            revision_number=1,
            label="makesClaim",
            description="incompatible stand-in",
            value_kind=ValueKind.STRING.value,
            datatype="xsd:string",
            cardinality="one",
            is_symmetric=False,
            is_transitive=False,
            metadata_json={},
            created_by_actor_id=actor.id,
        )
        db_session.add(rev)
        db_session.flush()
        pred.current_revision_id = rev.id
        db_session.add(
            OntologyPredicateDomain(
                id=stable_seed_id("predicate_domain", CORE_NAMESPACE_KEY, "makesClaim", "Document"),
                predicate_revision_id=rev.id,
                class_id=document.id,
            )
        )
        db_session.flush()

    try:
        ensure_claim_ontology(db_session)
        raise AssertionError("expected OntologySeedConflictError")
    except OntologySeedConflictError as exc:
        assert "makesClaim" in str(exc)
        assert exc.details.get("predicate_key") == "makesClaim"
