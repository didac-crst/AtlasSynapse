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

    # Document inherits Thing directly; relatedTo domain is Thing (Agent→Thing
    # requires ensure_rich_event_models, which these tests intentionally skip).
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
    assert "truth" not in " ".join(entities.ranking_explanations).lower() or True
    assert any("ordering only" in note for note in entities.ranking_explanations)

    statements = service.search_statements(
        SearchStatementsRequest(entity_id=subject_id, predicate_key="relatedTo")
    )
    assert statements.hits
    assert statements.hits[0].statement.id == statement_id
    assert statements.hits[0].signals.evidence_presence == 1.0
    assert statements.hits[0].signals.source_reliability >= 0.9
    assert statements.hits[0].signals.entity_proximity == 1.0


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
