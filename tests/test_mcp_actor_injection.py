"""MCP injects a configured actor_key into mutation envelopes."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from semantic_memory.config import get_settings
from semantic_memory.mcp.tools import EntityMCPTools, prepare_mcp_mutation_payload
from semantic_memory.services.actors import ActorService


@pytest.fixture
def mcp_chatgpt_settings(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("MCP_ACTOR_KEY", "chatgpt")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_prepare_mcp_mutation_payload_overrides_client_actor_key(
    db_session: Session, mcp_chatgpt_settings: object
) -> None:
    del mcp_chatgpt_settings
    prepared = prepare_mcp_mutation_payload(
        db_session,
        {
            "actor_key": "guessed-wrong",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "canonical_name": "X",
            "class_key": "Person",
        },
    )
    assert prepared["actor_key"] == "chatgpt"
    assert ActorService(db_session).find_by_key("chatgpt") is not None


def test_mcp_create_entity_without_actor_key(engine: Engine, mcp_chatgpt_settings: object) -> None:
    del mcp_chatgpt_settings
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        tools = EntityMCPTools(session)
        result = tools.create_entity(
            {
                "request_id": str(uuid.uuid4()),
                "idempotency_key": str(uuid.uuid4()),
                "canonical_name": f"MCP Auto Actor {uuid.uuid4()}",
                "class_key": "Person",
            }
        )
        assert result.get("error_code") is None
        assert result["outcome"] == "CREATE"
        assert ActorService(session).find_by_key("chatgpt") is not None
    finally:
        session.close()
