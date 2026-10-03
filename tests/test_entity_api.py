"""HTTP/MCP contract tests for entity operations."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from semantic_memory.config import get_settings
from semantic_memory.mcp.tools import EntityMCPTools
from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES, Capability
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.services.actors import ActorService


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


def test_http_actor_ensure_requires_admin_token(client: TestClient) -> None:
    denied = client.post(
        "/v1/actors/ensure",
        json={
            "key": "unprivileged",
            "actor_type": "agent",
            "capabilities": [Capability.ADMIN.value],
            "status": "active",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "UNAUTHORIZED_OPERATION"

    with_token = client.post(
        "/v1/actors/ensure",
        headers=_admin_headers(),
        json={
            "key": "still-no-admin",
            "actor_type": "agent",
            "capabilities": [Capability.ADMIN.value],
            "status": "active",
        },
    )
    assert with_token.status_code == 403
    assert with_token.json()["error_code"] == "UNAUTHORIZED_OPERATION"


def test_admin_api_token_defaults_fail_closed() -> None:
    get_settings.cache_clear()
    try:
        from semantic_memory.config import Settings

        assert Settings(admin_api_token="").admin_api_token == ""
        assert Settings.model_fields["admin_api_token"].default == ""
    finally:
        get_settings.cache_clear()


def test_http_create_and_get_entity(client: TestClient) -> None:
    _ensure_writer_http(client)
    payload = {
        "actor_key": "api-writer",
        "request_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "canonical_name": f"Didac API {uuid.uuid4()}",
        "class_key": "Person",
    }
    created = client.post("/v1/entities", json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["outcome"] == "CREATE"
    entity_id = body["entity"]["id"]

    fetched = client.get(f"/v1/entities/{entity_id}")
    assert fetched.status_code == 200
    assert fetched.json()["canonical_name"] == payload["canonical_name"]


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


def test_http_validation_error_maps_envelope(client: TestClient) -> None:
    _ensure_writer_http(client)
    response = client.post(
        "/v1/entities",
        json={
            "actor_key": "api-writer",
            "request_id": "not-a-uuid",
            "idempotency_key": "k",
            "canonical_name": "X",
            "class_key": "Person",
        },
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_FAILED"


def test_mcp_create_entity_tool(engine: Engine) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        ActorService(session).ensure(
            ActorEnsureRequest(
                key="mcp-writer",
                actor_type=ActorType.AGENT,
            )
        )
        session.commit()
        tools = EntityMCPTools(session)
        result = tools.create_entity(
            {
                "actor_key": "mcp-writer",
                "request_id": str(uuid.uuid4()),
                "idempotency_key": str(uuid.uuid4()),
                "canonical_name": f"Airbus MCP {uuid.uuid4()}",
                "class_key": "Organization",
            }
        )
        assert result["outcome"] == "CREATE"
        assert result["entity"]["canonical_name"].startswith("Airbus MCP")
    finally:
        session.close()


def test_mcp_validation_failure_rolls_back(engine: Engine) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        ActorService(session).ensure(
            ActorEnsureRequest(
                key="mcp-validate",
                actor_type=ActorType.AGENT,
            )
        )
        session.commit()
        tools = EntityMCPTools(session)
        result = tools.create_entity({"actor_key": "mcp-validate"})
        assert result["error_code"] == "VALIDATION_FAILED"
    finally:
        session.close()
