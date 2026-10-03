"""HTTP/MCP contract tests for entity operations."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from semantic_memory.mcp.tools import EntityMCPTools
from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.services.actors import ActorService


def _ensure_writer_http(client: TestClient) -> None:
    response = client.post(
        "/v1/actors/ensure",
        json={
            "key": "api-writer",
            "actor_type": "agent",
            "capabilities": [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            "status": "active",
        },
    )
    assert response.status_code == 200


def test_http_create_and_get_entity(client: TestClient) -> None:
    _ensure_writer_http(client)
    payload = {
        "actor_key": "api-writer",
        "request_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "canonical_name": "Didac",
        "class_key": "Person",
    }
    created = client.post("/v1/entities", json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["outcome"] == "CREATE"
    entity_id = body["entity"]["id"]

    fetched = client.get(f"/v1/entities/{entity_id}")
    assert fetched.status_code == 200
    assert fetched.json()["canonical_name"] == "Didac"


def test_http_unknown_class_maps_error(client: TestClient) -> None:
    _ensure_writer_http(client)
    response = client.post(
        "/v1/entities",
        json={
            "actor_key": "api-writer",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "canonical_name": "X",
            "class_key": "NoSuchClass",
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "UNKNOWN_CLASS"
    assert body["retryable"] is False


def test_mcp_create_entity_tool(engine: Engine) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        ActorService(session).ensure(
            ActorEnsureRequest(
                key="mcp-writer",
                actor_type=ActorType.AGENT,
                capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            )
        )
        session.commit()
        tools = EntityMCPTools(session)
        result = tools.create_entity(
            {
                "actor_key": "mcp-writer",
                "request_id": str(uuid.uuid4()),
                "idempotency_key": str(uuid.uuid4()),
                "canonical_name": "Airbus",
                "class_key": "Organization",
            }
        )
        assert result["outcome"] == "CREATE"
        assert result["entity"]["canonical_name"] == "Airbus"
    finally:
        session.close()
