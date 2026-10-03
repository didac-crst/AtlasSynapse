"""HTTP/MCP contract tests for statement assertion."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from semantic_memory.config import get_settings
from semantic_memory.mcp.tools import StatementMCPTools
from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": get_settings().admin_api_token}


def _ensure_writer_http(client: TestClient) -> None:
    response = client.post(
        "/v1/actors/ensure",
        headers=_admin_headers(),
        json={
            "key": "api-writer",
            "actor_type": "agent",
            "capabilities": [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            "status": "active",
        },
    )
    assert response.status_code == 200


def _create_document_http(client: TestClient) -> str:
    created = client.post(
        "/v1/entities",
        json={
            "actor_key": "api-writer",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "canonical_name": f"API Doc {uuid.uuid4()}",
            "class_key": "Document",
        },
    )
    assert created.status_code == 200
    return created.json()["entity"]["id"]


def test_http_assert_and_get_statement(client: TestClient) -> None:
    _ensure_writer_http(client)
    subject_id = _create_document_http(client)
    asserted = client.post(
        "/v1/statements",
        json={
            "actor_key": "api-writer",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "subject_entity_id": subject_id,
            "predicate_key": "description",
            "object_string": "HTTP asserted",
        },
    )
    assert asserted.status_code == 200
    body = asserted.json()
    assert body["outcome"] == "CREATE"
    statement_id = body["statement"]["id"]

    fetched = client.get(f"/v1/statements/{statement_id}")
    assert fetched.status_code == 200
    assert fetched.json()["object_string"] == "HTTP asserted"


def test_http_domain_violation_maps_error(client: TestClient) -> None:
    _ensure_writer_http(client)
    person = client.post(
        "/v1/entities",
        json={
            "actor_key": "api-writer",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "canonical_name": f"Person {uuid.uuid4()}",
            "class_key": "Person",
        },
    )
    assert person.status_code == 200
    response = client.post(
        "/v1/statements",
        json={
            "actor_key": "api-writer",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "subject_entity_id": person.json()["entity"]["id"],
            "predicate_key": "occurredAt",
            "object_datetime": "2026-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "DOMAIN_VIOLATION"


def test_mcp_assert_statement_tool(engine: Engine) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        ActorService(session).ensure(
            ActorEnsureRequest(key="mcp-writer", actor_type=ActorType.AGENT)
        )
        session.commit()
        entity = EntityService(session).create_entity(
            CreateEntityRequest(
                actor_key="mcp-writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                canonical_name=f"MCP Doc {uuid.uuid4()}",
                class_key="Document",
            )
        )
        session.commit()
        assert entity.entity is not None
        tools = StatementMCPTools(session)
        result = tools.assert_statement(
            {
                "actor_key": "mcp-writer",
                "request_id": str(uuid.uuid4()),
                "idempotency_key": str(uuid.uuid4()),
                "subject_entity_id": str(entity.entity.id),
                "predicate_key": "description",
                "object_string": "via MCP",
            }
        )
        assert result["outcome"] == "CREATE"
        assert result["statement"]["object_string"] == "via MCP"
    finally:
        session.close()
