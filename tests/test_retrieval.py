"""Phase 13 retrieval tests (no LLM / vector provider required)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from semantic_memory.config import get_settings
from semantic_memory.mcp.server import MCPPlaceholder
from semantic_memory.mcp.tools import RetrievalMCPTools
from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.provenance import AddEvidenceRequest, EnsureSourceRequest, SourceInput
from semantic_memory.schemas.retrieval import (
    RelevantContextRequest,
    SearchEntitiesRequest,
    SearchSemanticMemoryRequest,
    SearchStatementsRequest,
)
from semantic_memory.schemas.statements import AssertStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.retrieval import RetrievalService
from semantic_memory.services.statements import StatementService


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": get_settings().admin_api_token}


def _ensure_writer(session: Session, key: str = "retrieval-writer") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def _seed_graph(session: Session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    writer = _ensure_writer(session)
    entities = EntityService(session)
    statements = StatementService(session)
    provenance = ProvenanceService(session)

    # Document inherits Thing directly; relatedTo domain/range is Thing.
    # Person also works via core Agent→Thing (no rich-event seed required).
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"p-{uuid.uuid4()}",
            canonical_name="Ada Retrieval",
            class_key="Document",
            aliases=["Ada R"],
        )
    )
    peer = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"o-{uuid.uuid4()}",
            canonical_name="Atlas Org",
            class_key="Document",
        )
    )
    assert subject.entity is not None and peer.entity is not None
    asserted = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"s-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer.entity.id,
        )
    )
    source = provenance.ensure_source(
        EnsureSourceRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"src-{uuid.uuid4()}",
            source_system="test",
            external_id=f"src-{uuid.uuid4()}",
            title="Retrieval Source",
            reliability=Decimal("0.9000"),
        )
    )
    provenance.add_evidence(
        AddEvidenceRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"ev-{uuid.uuid4()}",
            statement_id=asserted.statement.id,
            source=SourceInput(source_id=source.source.id),
            excerpt="Ada works with Atlas Org",
        )
    )
    return subject.entity.id, peer.entity.id, asserted.statement.id


def test_search_entities_and_statements_with_signals(db_session: Session) -> None:
    subject_id, peer_id, statement_id = _seed_graph(db_session)
    service = RetrievalService(db_session)

    entities = service.search_entities(SearchEntitiesRequest(query="Ada"))
    assert entities.hits
    assert entities.hits[0].entity.id == subject_id
    assert entities.hits[0].signals.lexical_relevance > 0
    assert any("not a truth score" in note.lower() for note in entities.ranking_explanations)
    assert any("ordering only" in note for note in entities.ranking_explanations)

    statements = service.search_statements(
        SearchStatementsRequest(entity_id=subject_id, predicate_key="relatedTo")
    )
    assert statements.hits
    assert statements.hits[0].statement.id == statement_id
    assert statements.hits[0].signals.evidence_presence == 1.0
    assert statements.hits[0].signals.source_reliability >= 0.9
    assert statements.hits[0].signals.entity_proximity == 1.0


def test_predicate_intent_prefers_holds_role_over_has_goal_on_historical_anchor(
    db_session: Session,
) -> None:
    """Soft predicate intent should break holdsRole vs hasGoal historical ties."""
    from datetime import UTC, datetime

    from semantic_memory.repositories.statements import StatementRepository

    writer = _ensure_writer(db_session, key="pred-intent-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)

    person = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"pi-person-{uuid.uuid4()}",
            canonical_name="PredIntentPerson",
            class_key="Document",
        )
    )
    role = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"pi-role-{uuid.uuid4()}",
            canonical_name="PredIntent Role",
            class_key="Document",
        )
    )
    goal = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"pi-goal-{uuid.uuid4()}",
            canonical_name="PredIntent Goal",
            class_key="Document",
        )
    )
    assert person.entity and role.entity and goal.entity
    # Use relatedTo as stand-in if holdsRole/hasGoal unavailable in smoke ontology —
    # production LAN bench covers real predicates; here we only check the scorer path
    # is wired (match_reasons). Full disambiguation is covered by unit + LAN bench.
    linked = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"pi-link-{uuid.uuid4()}",
            subject_entity_id=person.entity.id,
            predicate_key="relatedTo",
            object_entity_id=role.entity.id,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert linked.statement is not None
    repo = StatementRepository(db_session)
    row = repo.get(linked.statement.id)
    assert row is not None
    # Mark superseded so historical anchor expansion includes it.
    other = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"pi-cur-{uuid.uuid4()}",
            subject_entity_id=person.entity.id,
            predicate_key="relatedTo",
            object_entity_id=role.entity.id,
            valid_from=datetime(2025, 9, 1, tzinfo=UTC),
        )
    )
    assert other.statement is not None
    repo.mark_superseded(row, replacement_id=other.statement.id)
    db_session.flush()

    service = RetrievalService(db_session)
    memory = service.search_semantic_memory(
        SearchSemanticMemoryRequest(query="PredIntentPerson previous role", limit=25)
    )
    assert any("predicate intent" in note.lower() for note in memory.ranking_explanations)
    assert any(
        hit.statement is not None and hit.statement.id == linked.statement.id for hit in memory.hits
    )


def test_anchor_1hop_reaches_superseded_holds_for_historical_query(
    db_session: Session,
) -> None:
    """Entity-valued superseded facts with empty object_string need 1-hop anchors."""
    from datetime import UTC, datetime

    from semantic_memory.repositories.statements import StatementRepository

    writer = _ensure_writer(db_session, key="anchor-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)

    person = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"a-person-{uuid.uuid4()}",
            canonical_name="Anchorman",
            class_key="Document",
        )
    )
    role = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"a-role-{uuid.uuid4()}",
            canonical_name="Anchor Role Title",
            class_key="Document",
        )
    )
    assert person.entity is not None and role.entity is not None
    current = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"a-cur-{uuid.uuid4()}",
            subject_entity_id=person.entity.id,
            predicate_key="relatedTo",
            object_entity_id=role.entity.id,
            valid_from=datetime(2025, 9, 1, tzinfo=UTC),
        )
    )
    prior = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"a-prior-{uuid.uuid4()}",
            subject_entity_id=person.entity.id,
            predicate_key="relatedTo",
            object_entity_id=role.entity.id,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert current.statement is not None and prior.statement is not None
    repo = StatementRepository(db_session)
    row = repo.get(prior.statement.id)
    assert row is not None
    repo.mark_superseded(row, replacement_id=current.statement.id)
    db_session.flush()

    service = RetrievalService(db_session)
    historical = service.search_semantic_memory(
        SearchSemanticMemoryRequest(
            query="Anchorman previous role",
            limit=25,
        )
    )
    hist_ids = {hit.statement.id: hit for hit in historical.hits if hit.statement is not None}
    assert prior.statement.id in hist_ids
    assert "anchor_1hop" in hist_ids[prior.statement.id].match_reasons
    assert any("1-hop anchor" in note.lower() for note in historical.ranking_explanations)

    # Current intent must not run historical anchor expansion / surface superseded.
    current_search = service.search_semantic_memory(
        SearchSemanticMemoryRequest(
            query="Anchorman current role",
            limit=25,
        )
    )
    current_ids = {hit.statement.id for hit in current_search.hits if hit.statement is not None}
    assert prior.statement.id not in current_ids
    assert any(
        "skipped (non-historical" in note.lower() or "non-historical intent" in note.lower()
        for note in current_search.ranking_explanations
    )


def test_current_intent_prefers_effective_over_unbounded_description(
    db_session: Session,
) -> None:
    writer = _ensure_writer(db_session, key="temporal-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    from datetime import UTC, datetime

    person = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"t-person-{uuid.uuid4()}",
            canonical_name="Temporal Person",
            class_key="Document",
        )
    )
    role = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"t-role-{uuid.uuid4()}",
            canonical_name="Temporal Role Title",
            class_key="Document",
        )
    )
    assert person.entity is not None and role.entity is not None
    effective = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"t-eff-{uuid.uuid4()}",
            subject_entity_id=person.entity.id,
            predicate_key="relatedTo",
            object_entity_id=role.entity.id,
            valid_from=datetime(2025, 9, 1, tzinfo=UTC),
        )
    )
    legacy = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"t-leg-{uuid.uuid4()}",
            subject_entity_id=role.entity.id,
            predicate_key="relatedTo",
            object_string=("Temporal Role Title at Org somewhere, started 1 January 2026"),
        )
    )
    assert effective.statement is not None and legacy.statement is not None

    service = RetrievalService(db_session)
    memory = service.search_semantic_memory(
        SearchSemanticMemoryRequest(query="Temporal Person current role", limit=25)
    )
    statement_ids = [hit.statement.id for hit in memory.hits if hit.statement is not None]
    assert effective.statement.id in statement_ids
    assert statement_ids.index(effective.statement.id) < statement_ids.index(legacy.statement.id)


def test_tokenized_multi_term_and_punctuation_insensitive_search(
    db_session: Session,
) -> None:
    subject_id, peer_id, statement_id = _seed_graph(db_session)
    service = RetrievalService(db_session)

    # Punctuation / question phrasing must not zero out a name token.
    who = service.search_entities(SearchEntitiesRequest(query="Who is Ada?"))
    assert who.hits
    assert who.hits[0].entity.id == subject_id
    assert who.hits[0].signals.lexical_relevance >= 0.7

    exact = service.search_entities(SearchEntitiesRequest(query="Ada Retrieval"))
    assert exact.hits
    assert exact.hits[0].entity.id == subject_id
    assert exact.hits[0].signals.lexical_relevance == 1.0

    # Multi-term query should surface both named entities without full-string containment.
    multi = service.search_semantic_memory(SearchSemanticMemoryRequest(query="Ada Atlas", limit=25))
    entity_ids = {hit.entity.id for hit in multi.hits if hit.entity is not None}
    assert subject_id in entity_ids
    assert peer_id in entity_ids

    # Entity-valued statements are searchable via subject/object names, not only object_string.
    by_object_name = service.search_statements(SearchStatementsRequest(query="Atlas Org", limit=25))
    assert statement_id in {hit.statement.id for hit in by_object_name.hits}


def test_neighborhood_timeline_explain_conflicts_compose(db_session: Session) -> None:
    subject_id, peer_id, _statement_id = _seed_graph(db_session)
    service = RetrievalService(db_session)

    neighborhood = service.get_entity_neighborhood(subject_id)
    assert neighborhood.edges
    assert neighborhood.edges[0].neighbor_entity_id == peer_id

    context = service.get_relevant_context(
        RelevantContextRequest(entity_id=subject_id, query="Ada")
    )
    assert context.entity is not None
    assert context.timeline is not None
    assert context.neighborhood is not None
    assert context.metadata["llm_used"] is False
    assert context.metadata["vector_search_used"] is False

    memory = service.search_semantic_memory(SearchSemanticMemoryRequest(query="Ada"))
    assert memory.vector_search_used is False
    assert any(hit.entity is not None for hit in memory.hits)


def test_retrieval_works_without_llm_or_vector_provider(db_session: Session) -> None:
    _seed_graph(db_session)
    service = RetrievalService(db_session)
    memory = service.search_semantic_memory(SearchSemanticMemoryRequest(query="Atlas"))
    assert memory.hits
    assert memory.vector_search_used is False
    for hit in memory.hits:
        # Signals are exposed; no opaque truth score field.
        assert hit.signals.notes


def test_http_and_mcp_retrieval(client: TestClient, db_session: Session) -> None:
    ensured = client.post(
        "/v1/actors/ensure",
        headers=_admin_headers(),
        json={
            "key": "http-retrieval",
            "actor_type": "agent",
            "capabilities": [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            "status": "active",
        },
    )
    assert ensured.status_code == 200
    created = client.post(
        "/v1/entities",
        json={
            "actor_key": "http-retrieval",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": f"http-ent-{uuid.uuid4()}",
            "canonical_name": "HTTP Search Person",
            "class_key": "Person",
        },
    )
    assert created.status_code == 200
    entity_id = created.json()["entity"]["id"]

    search = client.get("/v1/entities/search", params={"query": "HTTP Search"})
    assert search.status_code == 200
    assert search.json()["hits"]

    neighborhood = client.get(f"/v1/entities/{entity_id}/neighborhood")
    assert neighborhood.status_code == 200

    memory = client.get("/v1/memory/search", params={"query": "HTTP Search"})
    assert memory.status_code == 200
    assert memory.json()["vector_search_used"] is False

    tools = MCPPlaceholder.from_settings().tools
    assert "search_entities" in tools
    assert "search_semantic_memory" in tools
    assert "get_relevant_context" in tools

    mcp = RetrievalMCPTools(db_session)
    mcp_result = mcp.search_entities({"query": "HTTP Search"})
    assert "hits" in mcp_result
    assert "error_code" not in mcp_result


def test_search_statements_subject_and_object_filters(db_session: Session) -> None:
    subject_id, peer_id, statement_id = _seed_graph(db_session)
    service = RetrievalService(db_session)

    as_subject = service.search_statements(
        SearchStatementsRequest(subject_entity_id=subject_id, predicate_key="relatedTo")
    )
    assert [hit.statement.id for hit in as_subject.hits] == [statement_id]
    assert all(hit.statement.subject_entity_id == subject_id for hit in as_subject.hits)

    as_object = service.search_statements(
        SearchStatementsRequest(object_entity_id=peer_id, predicate_key="relatedTo")
    )
    assert [hit.statement.id for hit in as_object.hits] == [statement_id]
    assert all(hit.statement.object_entity_id == peer_id for hit in as_object.hits)

    # Subject filter must not return statements where the entity is only the object.
    only_as_object = service.search_statements(SearchStatementsRequest(subject_entity_id=peer_id))
    assert all(hit.statement.subject_entity_id == peer_id for hit in only_as_object.hits)
    assert statement_id not in {hit.statement.id for hit in only_as_object.hits}

    only_as_subject_object = service.search_statements(
        SearchStatementsRequest(object_entity_id=subject_id)
    )
    assert statement_id not in {hit.statement.id for hit in only_as_subject_object.hits}


def test_search_statements_unknown_field_rejected(db_session: Session) -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchStatementsRequest.model_validate(
            {"subject_entity_id": str(uuid.uuid4()), "not_a_real_filter": True}
        )

    mcp = RetrievalMCPTools(db_session)
    result = mcp.search_statements({"subject_entity_id": str(uuid.uuid4()), "bogus_field": "x"})
    assert result.get("error_code") == "VALIDATION_FAILED"


def test_search_statements_pagination_is_stable(db_session: Session) -> None:
    writer = _ensure_writer(db_session, key="page-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"page-s-{uuid.uuid4()}",
            canonical_name="Page Subject",
            class_key="Document",
        )
    )
    assert subject.entity is not None
    created_ids: list[uuid.UUID] = []
    for i in range(5):
        peer = entities.create_entity(
            CreateEntityRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"page-o-{i}-{uuid.uuid4()}",
                canonical_name=f"Page Peer {i}",
                class_key="Document",
            )
        )
        assert peer.entity is not None
        asserted = statements.assert_statement(
            AssertStatementRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"page-st-{i}-{uuid.uuid4()}",
                subject_entity_id=subject.entity.id,
                predicate_key="relatedTo",
                object_entity_id=peer.entity.id,
            )
        )
        assert asserted.statement is not None
        created_ids.append(asserted.statement.id)

    service = RetrievalService(db_session)
    page0 = service.search_statements(
        SearchStatementsRequest(subject_entity_id=subject.entity.id, limit=2, offset=0)
    )
    page1 = service.search_statements(
        SearchStatementsRequest(subject_entity_id=subject.entity.id, limit=2, offset=2)
    )
    page0_again = service.search_statements(
        SearchStatementsRequest(subject_entity_id=subject.entity.id, limit=2, offset=0)
    )

    assert page0.total == 5
    assert page0.limit == 2
    assert page0.offset == 0
    assert len(page0.hits) == 2
    assert len(page1.hits) == 2
    ids0 = [hit.statement.id for hit in page0.hits]
    ids1 = [hit.statement.id for hit in page1.hits]
    assert set(ids0).isdisjoint(ids1)
    assert ids0 == [hit.statement.id for hit in page0_again.hits]

    all_pages: list[uuid.UUID] = []
    offset = 0
    while True:
        page = service.search_statements(
            SearchStatementsRequest(subject_entity_id=subject.entity.id, limit=2, offset=offset)
        )
        all_pages.extend(hit.statement.id for hit in page.hits)
        if offset + page.limit >= page.total:
            break
        offset += page.limit
    assert len(all_pages) == page0.total
    assert len(set(all_pages)) == page0.total


def test_search_statements_temporal_state_filter(db_session: Session) -> None:
    from datetime import UTC, datetime

    from semantic_memory.schemas.retrieval import TemporalState

    writer = _ensure_writer(db_session, key="temporal-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    suffix = uuid.uuid4().hex[:8]
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"ts-s-{uuid.uuid4()}",
            canonical_name=f"Temporal Subject {suffix}",
            class_key="Document",
        )
    )
    peer = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"ts-o-{uuid.uuid4()}",
            canonical_name=f"Temporal Peer A {suffix}",
            class_key="Document",
        )
    )
    assert subject.entity is not None and peer.entity is not None
    bounded = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"ts-b-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer.entity.id,
            valid_from=datetime(2010, 1, 1, tzinfo=UTC),
            valid_to=datetime(2012, 1, 1, tzinfo=UTC),
        )
    )
    assert bounded.statement is not None
    # Second peer so we can assert another relatedTo without colliding on identity.
    peer2 = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"ts-o2-{uuid.uuid4()}",
            canonical_name=f"Temporal Peer B {suffix}",
            class_key="Document",
        )
    )
    assert peer2.entity is not None
    unbounded = statements.assert_statement(
        AssertStatementRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"ts-u-{uuid.uuid4()}",
            subject_entity_id=subject.entity.id,
            predicate_key="relatedTo",
            object_entity_id=peer2.entity.id,
        )
    )
    assert unbounded.statement is not None

    service = RetrievalService(db_session)
    bounded_hits = service.search_statements(
        SearchStatementsRequest(
            subject_entity_id=subject.entity.id, temporal_state=TemporalState.BOUNDED
        )
    )
    assert {hit.statement.id for hit in bounded_hits.hits} == {bounded.statement.id}

    unbounded_hits = service.search_statements(
        SearchStatementsRequest(
            subject_entity_id=subject.entity.id, temporal_state=TemporalState.UNBOUNDED
        )
    )
    assert {hit.statement.id for hit in unbounded_hits.hits} == {unbounded.statement.id}


def test_http_rejects_unknown_statement_search_query_param(client: TestClient) -> None:
    response = client.get(
        "/v1/statements/search",
        params={"subject_entity_id": str(uuid.uuid4()), "not_supported": "1"},
    )
    assert response.status_code == 422


def test_neighborhood_batched_ranking_matches_solo_scoring(db_session: Session) -> None:
    """Hard merge gate: batching must not change ranking_score or edge order."""
    from datetime import UTC, datetime

    from semantic_memory.models import StatementStatus

    writer = _ensure_writer(db_session, key="rank-gate-writer")
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    provenance = ProvenanceService(db_session)
    suffix = uuid.uuid4().hex[:8]
    subject = entities.create_entity(
        CreateEntityRequest(
            actor_key=writer,
            request_id=uuid.uuid4(),
            idempotency_key=f"rg-s-{uuid.uuid4()}",
            canonical_name=f"Rank Gate Subject {suffix}",
            class_key="Document",
        )
    )
    assert subject.entity is not None
    subject_id = subject.entity.id
    reliabilities = [Decimal("0.1000"), Decimal("0.9500"), Decimal("0.5000"), None]
    statement_ids: list[uuid.UUID] = []
    for i, rel in enumerate(reliabilities):
        peer = entities.create_entity(
            CreateEntityRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"rg-o-{i}-{uuid.uuid4()}",
                canonical_name=f"Rank Gate Peer {i} {suffix}",
                class_key="Document",
            )
        )
        assert peer.entity is not None
        asserted = statements.assert_statement(
            AssertStatementRequest(
                actor_key=writer,
                request_id=uuid.uuid4(),
                idempotency_key=f"rg-st-{i}-{uuid.uuid4()}",
                subject_entity_id=subject_id,
                predicate_key="relatedTo",
                object_entity_id=peer.entity.id,
            )
        )
        assert asserted.statement is not None
        statement_ids.append(asserted.statement.id)
        if rel is not None:
            source = provenance.ensure_source(
                EnsureSourceRequest(
                    actor_key=writer,
                    request_id=uuid.uuid4(),
                    idempotency_key=f"rg-src-{i}-{uuid.uuid4()}",
                    source_system="test",
                    external_id=f"rg-src-{i}-{uuid.uuid4()}",
                    title=f"Rank Source {i}",
                    reliability=rel,
                )
            )
            provenance.add_evidence(
                AddEvidenceRequest(
                    actor_key=writer,
                    request_id=uuid.uuid4(),
                    idempotency_key=f"rg-ev-{i}-{uuid.uuid4()}",
                    statement_id=asserted.statement.id,
                    source=SourceInput(source_id=source.source.id),
                    excerpt=f"evidence {i}",
                )
            )

    service = RetrievalService(db_session)
    as_of = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    identity_ids = set(service._entities.identity_group_ids(subject_id))
    survivor_id = service._entities.resolve_survivor_id(subject_id)
    timeline = service._statements.list_for_entity_timeline(list(identity_ids))
    candidates = [
        row
        for row in timeline
        if row.status == StatementStatus.ASSERTED.value and row.id in set(statement_ids)
    ]
    assert len(candidates) == len(statement_ids)

    evidence_stats = service._provenance_repo.evidence_stats_for_statements(
        [row.id for row in candidates]
    )
    specificity_cache: dict[uuid.UUID, float] = {}
    batched_scores: dict[uuid.UUID, float] = {}
    for row in candidates:
        count, reliability = evidence_stats.get(row.id, (0, 0.0))
        batched, _ = service._score_statement(
            row,
            query=None,
            focus_entity_id=survivor_id,
            now=as_of,
            evidence_count=count,
            source_reliability=reliability,
            specificity_cache=specificity_cache,
        )
        solo, _ = service._score_statement(
            row,
            query=None,
            focus_entity_id=survivor_id,
            now=as_of,
            evidence_count=None,
            source_reliability=None,
            specificity_cache=None,
        )
        assert batched.ranking_score == solo.ranking_score
        assert batched.evidence_presence == solo.evidence_presence
        assert batched.source_reliability == solo.source_reliability
        assert batched.ontology_specificity == solo.ontology_specificity
        batched_scores[row.id] = batched.ranking_score

    expected_order = sorted(
        batched_scores.keys(), key=lambda sid: batched_scores[sid], reverse=True
    )
    neighborhood = service.get_entity_neighborhood(subject_id, limit=50, as_of=as_of)
    got_ids = [edge.statement.id for edge in neighborhood.edges]
    assert got_ids == expected_order
    # Distinctive order: highest reliability edge ahead of unevidenced.
    high_rel_id = statement_ids[1]
    no_ev_id = statement_ids[3]
    assert got_ids.index(high_rel_id) < got_ids.index(no_ev_id)


def test_resolve_survivor_ids_matches_single(db_session: Session) -> None:
    from semantic_memory.repositories.entities import EntityRepository

    subject_id, peer_id, _ = _seed_graph(db_session)
    entities = EntityRepository(db_session)
    bulk = entities.resolve_survivor_ids([subject_id, peer_id, subject_id])
    assert bulk[subject_id] == entities.resolve_survivor_id(subject_id)
    assert bulk[peer_id] == entities.resolve_survivor_id(peer_id)
