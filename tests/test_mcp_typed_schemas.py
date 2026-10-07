"""MCP ontology tools advertise typed payload schemas."""

from __future__ import annotations

import uuid

from semantic_memory.mcp.schema_utils import mcp_payload_schema
from semantic_memory.mcp.server import build_mcp_tools
from semantic_memory.schemas.proposals import ProposePredicateRequest
from semantic_memory.schemas.semantic_review import AnswerSemanticClarificationRequest


def test_mcp_payload_schema_omits_actor_key() -> None:
    schema = mcp_payload_schema(ProposePredicateRequest)
    payload = schema["properties"]["payload"]
    assert "actor_key" not in payload["properties"]
    assert "domain_keys" in payload["properties"]
    assert "range_keys" in payload["properties"]
    assert "value_kind" in payload["properties"]
    assert "request_id" in payload["required"]
    assert "idempotency_key" in payload["required"]
    assert "actor_key" not in payload.get("required", [])


def test_propose_predicate_accepts_read_api_class_key_aliases() -> None:
    req = ProposePredicateRequest.model_validate(
        {
            "actor_key": "writer",
            "request_id": uuid.uuid4(),
            "idempotency_key": "idem-1",
            "key": "studiedAtAlias",
            "value_kind": "entity",
            "domain_class_keys": ["Person"],
            "range_class_keys": ["Organization"],
        }
    )
    assert req.domain_keys == ["Person"]
    assert req.range_keys == ["Organization"]


def test_answer_semantic_clarification_schema_documents_response() -> None:
    schema = mcp_payload_schema(AnswerSemanticClarificationRequest)
    props = schema["properties"]["payload"]["properties"]
    assert "response" in props
    assert "answer" not in props
    desc = props["response"].get("description") or ""
    assert "not `answer`" in desc or "not answer" in desc.lower()


def test_ontology_mcp_tools_use_typed_payload_schemas() -> None:
    from semantic_memory.config import Settings

    tools = {
        tool.name: tool
        for tool in build_mcp_tools(settings=Settings(mcp_tool_surface="all"))
    }
    for name in (
        "propose_predicate",
        "propose_class",
        "apply_ontology_proposal",
        "answer_semantic_clarification",
        "answer_identity_clarification",
        "search_statements",
        "supersede_statement",
        "correct_statement",
        "retract_statement",
    ):
        schema = tools[name].input_schema
        assert schema["required"] == ["payload"]
        payload = schema["properties"]["payload"]
        assert payload["type"] == "object"
        assert payload.get("additionalProperties") is False
        assert "actor_key" not in payload["properties"]

    pred = tools["propose_predicate"].input_schema["properties"]["payload"]["properties"]
    assert "domain_keys" in pred
    assert "range_keys" in pred
    answer = tools["answer_semantic_clarification"].input_schema["properties"]["payload"][
        "properties"
    ]
    assert "response" in answer

    search = tools["search_statements"].input_schema["properties"]["payload"]["properties"]
    assert "subject_entity_id" in search
    assert "object_entity_id" in search
    assert "offset" in search
    assert "temporal_state" in search

    supersede = tools["supersede_statement"].input_schema["properties"]["payload"]["properties"]
    assert "previous_statement_id" in supersede
    assert "predicate_key" in supersede
    assert "request_id" in supersede

    retract = tools["retract_statement"].input_schema["properties"]["payload"]["properties"]
    assert "statement_id" in retract
    assert "reason" in retract
    assert "actor_key" not in retract
