"""MCP transport server (stdio) over thin tool adapters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.db import configure_engine, get_session_factory
from semantic_memory.mcp.schema_utils import mcp_payload_schema
from semantic_memory.mcp.stdio import StdioMCPServer, ToolSpec
from semantic_memory.mcp.tools import (
    EntityMCPTools,
    FeedbackMCPTools,
    OntologyMCPTools,
    RetrievalMCPTools,
    StatementMCPTools,
)
from semantic_memory.runtime_info import runtime_config
from semantic_memory.schemas.proposals import (
    ApplyProposalRequest,
    ProposeAliasRequest,
    ProposeClassParentRequest,
    ProposeClassRequest,
    ProposeConstraintRequest,
    ProposePredicateRequest,
)
from semantic_memory.schemas.retrieval import SearchStatementsRequest
from semantic_memory.schemas.semantic_review import (
    AnswerSemanticClarificationRequest,
    ChallengeOntologyReviewRequest,
)
from semantic_memory.schemas.statements import (
    CorrectStatementRequest,
    RetractStatementRequest,
    SupersedeStatementRequest,
)
from semantic_memory.schemas.write_clarifications import AnswerIdentityClarificationRequest

REGISTERED_TOOL_NAMES: tuple[str, ...] = (
    "get_runtime_config",
    "create_entity",
    "get_entity",
    "search_entities",
    "get_entity_neighborhood",
    "merge_entity",
    "add_entity_alias",
    "assert_statement",
    "get_statement",
    "search_statements",
    "explain_statement",
    "ensure_source",
    "ingest_source_content",
    "get_source_content",
    "search_source_content",
    "add_evidence",
    "supersede_statement",
    "correct_statement",
    "retract_statement",
    "get_timeline",
    "find_conflicts",
    "assert_batch",
    "search_semantic_memory",
    "get_relevant_context",
    "get_class",
    "get_predicate",
    "search_ontology",
    "get_ontology_context",
    "get_proposal",
    "propose_class",
    "propose_predicate",
    "propose_constraint",
    "propose_alias",
    "propose_class_parent",
    "apply_ontology_proposal",
    "challenge_ontology_review",
    "answer_semantic_clarification",
    "answer_identity_clarification",
    "report_feedback",
)

_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "payload": {"type": "object", "additionalProperties": True},
    },
    "required": ["payload"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class MCPServerInfo:
    """Describes the configured MCP transport and registered tool names."""

    transport: str
    ready: bool = True
    message: str = (
        "Stdio MCP transport exposing entity, statement, provenance, conflict, "
        "merge, supersession, retraction, timeline, retrieval, ontology read, "
        "proposal, and feedback tools."
    )
    tools: tuple[str, ...] = field(default_factory=lambda: REGISTERED_TOOL_NAMES)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> MCPServerInfo:
        cfg = settings or get_settings()
        return cls(transport=cfg.mcp_transport)


# Backward-compatible alias used by older imports/tests.
MCPPlaceholder = MCPServerInfo


@contextmanager
def _session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def _call(operation: Any) -> dict[str, Any]:
    with _session() as session:
        result = operation(session)
        if not isinstance(result, dict):
            raise TypeError("MCP tool handlers must return a dict")
        return result


def _get_class(
    class_key: str | None = None,
    class_id: str | None = None,
    alias: str | None = None,
    namespace_key: str = "core",
) -> dict[str, Any]:
    return _call(
        lambda s: OntologyMCPTools(s).get_class(
            class_key=class_key,
            class_id=class_id,
            alias=alias,
            namespace_key=namespace_key,
        )
    )


def _get_predicate(
    predicate_key: str | None = None,
    predicate_id: str | None = None,
    alias: str | None = None,
    namespace_key: str = "core",
) -> dict[str, Any]:
    return _call(
        lambda s: OntologyMCPTools(s).get_predicate(
            predicate_key=predicate_key,
            predicate_id=predicate_id,
            alias=alias,
            namespace_key=namespace_key,
        )
    )


def _get_ontology_context(
    class_key: str | None = None,
    class_id: str | None = None,
    alias: str | None = None,
    namespace_key: str = "core",
) -> dict[str, Any]:
    return _call(
        lambda s: OntologyMCPTools(s).get_ontology_context(
            class_key=class_key,
            class_id=class_id,
            alias=alias,
            namespace_key=namespace_key,
        )
    )


def _payload_tool(
    name: str,
    description: str,
    call: Any,
    *,
    request_model: type[Any] | None = None,
) -> ToolSpec:
    def handler(*, payload: dict[str, Any]) -> dict[str, Any]:
        with _session() as session:
            result = call(session, payload)
            if not isinstance(result, dict):
                raise TypeError("MCP tool handlers must return a dict")
            return result

    return ToolSpec(
        name=name,
        description=description,
        handler=handler,
        input_schema=(
            mcp_payload_schema(request_model) if request_model is not None else _PAYLOAD_SCHEMA
        ),
    )


def build_mcp_tools() -> list[ToolSpec]:
    """Build tool specs bound to per-call database sessions."""
    return [
        ToolSpec(
            name="get_runtime_config",
            description=(
                "Return non-secret AtlasSynapse runtime config for deploy parity checks "
                "(semantic_review_mode, version, model thresholds)."
            ),
            handler=lambda: runtime_config(),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        _payload_tool(
            "create_entity",
            "Create a typed knowledge entity. Set dry_run=true to preview "
            "MATCH/CREATE/CLARIFY without persisting.",
            lambda session, payload: EntityMCPTools(session).create_entity(payload),
        ),
        ToolSpec(
            name="get_entity",
            description="Fetch an entity by id.",
            handler=lambda entity_id: _call(lambda s: EntityMCPTools(s).get_entity(entity_id)),
            input_schema={
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "required": ["entity_id"],
            },
        ),
        _payload_tool(
            "search_entities",
            "Search entities with ranking signals.",
            lambda session, payload: RetrievalMCPTools(session).search_entities(payload),
        ),
        ToolSpec(
            name="get_entity_neighborhood",
            description="List neighboring entities.",
            handler=lambda entity_id, limit=50: _call(
                lambda s: RetrievalMCPTools(s).get_entity_neighborhood(entity_id, limit=limit)
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["entity_id"],
            },
        ),
        _payload_tool(
            "merge_entity",
            "Explicitly merge two entities. Copies source names/aliases onto the "
            "target so future create/search by those names reuse the survivor.",
            lambda session, payload: EntityMCPTools(session).merge_entity(payload),
        ),
        _payload_tool(
            "add_entity_alias",
            "Add an alternate name/alias to an existing active entity "
            "(e.g. add 'Didac' to 'Didac Cristobal').",
            lambda session, payload: EntityMCPTools(session).add_entity_alias(payload),
        ),
        _payload_tool(
            "assert_statement",
            "Assert a typed statement. Pass subject_entity_id/object_entity_id when "
            "known, or subject/object EntityInput (canonical_name + class_key) and let "
            "the server MATCH/CREATE/CLARIFY. Do not resolve or create entities yourself. "
            "Set dry_run=true to preview the full decision path without persisting.",
            lambda session, payload: StatementMCPTools(session).assert_statement(payload),
        ),
        ToolSpec(
            name="get_statement",
            description="Fetch a statement by id.",
            handler=lambda statement_id: _call(
                lambda s: StatementMCPTools(s).get_statement(statement_id)
            ),
            input_schema={
                "type": "object",
                "properties": {"statement_id": {"type": "string"}},
                "required": ["statement_id"],
            },
        ),
        _payload_tool(
            "search_statements",
            "Search statements with typed filters and stable pagination "
            "(created_at DESC, id DESC). Use subject_entity_id or object_entity_id "
            "for one-end filters; entity_id matches either end. Unknown fields fail "
            "validation. Optional temporal_state: bounded|open_end|open_start|unbounded.",
            lambda session, payload: RetrievalMCPTools(session).search_statements(payload),
            request_model=SearchStatementsRequest,
        ),
        ToolSpec(
            name="explain_statement",
            description="Explain a statement with evidence.",
            handler=lambda statement_id: _call(
                lambda s: StatementMCPTools(s).explain_statement(statement_id)
            ),
            input_schema={
                "type": "object",
                "properties": {"statement_id": {"type": "string"}},
                "required": ["statement_id"],
            },
        ),
        _payload_tool(
            "ensure_source",
            "Ensure a reusable provenance source (create or reuse by identity).",
            lambda session, payload: StatementMCPTools(session).ensure_source(payload),
        ),
        _payload_tool(
            "ingest_source_content",
            "Ingest source content. Prefer content + content_format (html|markdown|text); "
            "AtlasSynapse preserves the exact original and deterministically canonicalizes "
            "(HTML→Markdown v1). Optional canonical_format defaults to markdown.",
            lambda session, payload: StatementMCPTools(session).ingest_source_content(payload),
        ),
        _payload_tool(
            "get_source_content",
            "Fetch latest or specific source content revision by source_id, "
            "document_entity_id, or revision_id.",
            lambda session, payload: StatementMCPTools(session).get_source_content(payload),
        ),
        _payload_tool(
            "search_source_content",
            "Lexical search over canonical source content; returns passages with "
            "revision-qualified locators.",
            lambda session, payload: StatementMCPTools(session).search_source_content(payload),
        ),
        _payload_tool(
            "add_evidence",
            "Attach evidence to a statement.",
            lambda session, payload: StatementMCPTools(session).add_evidence(payload),
        ),
        _payload_tool(
            "supersede_statement",
            "Atomically supersede an asserted statement with a replacement. "
            "Omitted subject/predicate/object/temporal fields default to the previous "
            "statement. Returns previous and new statement ids. Actor is injected.",
            lambda session, payload: StatementMCPTools(session).supersede_statement(payload),
            request_model=SupersedeStatementRequest,
        ),
        _payload_tool(
            "correct_statement",
            "Correct temporal validity or object via supersession (immutable history). "
            "Copies SPO from statement_id; set valid_from/valid_to/observed_at/object_* "
            "overrides (null clears a bound). Prefer this over assert+manual cleanup.",
            lambda session, payload: StatementMCPTools(session).correct_statement(payload),
            request_model=CorrectStatementRequest,
        ),
        _payload_tool(
            "retract_statement",
            "Retract an asserted statement (soft; does not delete). Idempotent. "
            "Optional reason is stored in statement metadata.",
            lambda session, payload: StatementMCPTools(session).retract_statement(payload),
            request_model=RetractStatementRequest,
        ),
        ToolSpec(
            name="get_timeline",
            description="Get an entity timeline.",
            handler=lambda entity_id: _call(lambda s: StatementMCPTools(s).get_timeline(entity_id)),
            input_schema={
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "required": ["entity_id"],
            },
        ),
        ToolSpec(
            name="find_conflicts",
            description="Find conflicts for an entity or statement.",
            handler=lambda entity_id=None, statement_id=None, status="open": _call(
                lambda s: StatementMCPTools(s).find_conflicts(
                    entity_id=entity_id,
                    statement_id=statement_id,
                    status=status,
                )
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "statement_id": {"type": "string"},
                    "status": {"type": "string", "default": "open"},
                },
            },
        ),
        _payload_tool(
            "assert_batch",
            "Atomically assert a batch of statements. Statement items may use "
            "EntityInput subject/object; ambiguous identity fails the whole batch "
            "with AMBIGUOUS_ENTITY and rolls back created rows. "
            "Set dry_run=true to preview without persisting.",
            lambda session, payload: StatementMCPTools(session).assert_batch(payload),
        ),
        _payload_tool(
            "search_semantic_memory",
            "Search semantic memory.",
            lambda session, payload: RetrievalMCPTools(session).search_semantic_memory(payload),
        ),
        _payload_tool(
            "get_relevant_context",
            "Compose relevant context for an entity.",
            lambda session, payload: RetrievalMCPTools(session).get_relevant_context(payload),
        ),
        ToolSpec(
            name="get_class",
            description="Look up an ontology class.",
            handler=_get_class,
            input_schema={
                "type": "object",
                "properties": {
                    "class_key": {"type": "string"},
                    "class_id": {"type": "string"},
                    "alias": {"type": "string"},
                    "namespace_key": {"type": "string", "default": "core"},
                },
            },
        ),
        ToolSpec(
            name="get_predicate",
            description="Look up an ontology predicate.",
            handler=_get_predicate,
            input_schema={
                "type": "object",
                "properties": {
                    "predicate_key": {"type": "string"},
                    "predicate_id": {"type": "string"},
                    "alias": {"type": "string"},
                    "namespace_key": {"type": "string", "default": "core"},
                },
            },
        ),
        ToolSpec(
            name="search_ontology",
            description="Search ontology classes and predicates.",
            handler=lambda query, namespace_key="core", limit=25: _call(
                lambda s: OntologyMCPTools(s).search_ontology(
                    query=query,
                    namespace_key=namespace_key,
                    limit=limit,
                )
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "namespace_key": {"type": "string"},
                    "limit": {"type": "integer", "default": 25},
                },
                "required": ["query"],
            },
        ),
        ToolSpec(
            name="get_ontology_context",
            description="Get conservative ontology context.",
            handler=_get_ontology_context,
            input_schema={
                "type": "object",
                "properties": {
                    "class_key": {"type": "string"},
                    "class_id": {"type": "string"},
                    "alias": {"type": "string"},
                    "namespace_key": {"type": "string", "default": "core"},
                },
            },
        ),
        ToolSpec(
            name="get_proposal",
            description="Fetch an ontology proposal.",
            handler=lambda proposal_id: _call(
                lambda s: OntologyMCPTools(s).get_proposal(proposal_id)
            ),
            input_schema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        _payload_tool(
            "propose_class",
            "Propose a new ontology class. On ambiguous overlap, response includes "
            "open_clarification_request with AtlasSynapse-issued clarification_request_id. "
            "Use namespace_key='smoke' for disposable tests (not production).",
            lambda session, payload: OntologyMCPTools(session).propose_class(payload),
            request_model=ProposeClassRequest,
        ),
        _payload_tool(
            "propose_predicate",
            "Propose a new ontology predicate. Write fields are domain_keys/range_keys "
            "(get_predicate returns domain_class_keys/range_class_keys; those aliases "
            "are also accepted). Use namespace_key='smoke' for disposable tests.",
            lambda session, payload: OntologyMCPTools(session).propose_predicate(payload),
            request_model=ProposePredicateRequest,
        ),
        _payload_tool(
            "propose_constraint",
            "Propose an ontology constraint.",
            lambda session, payload: OntologyMCPTools(session).propose_constraint(payload),
            request_model=ProposeConstraintRequest,
        ),
        _payload_tool(
            "propose_alias",
            "Propose an ontology alias.",
            lambda session, payload: OntologyMCPTools(session).propose_alias(payload),
            request_model=ProposeAliasRequest,
        ),
        _payload_tool(
            "propose_class_parent",
            "Propose a class parent link.",
            lambda session, payload: OntologyMCPTools(session).propose_class_parent(payload),
            request_model=ProposeClassParentRequest,
        ),
        _payload_tool(
            "apply_ontology_proposal",
            "Commit an applyable ontology proposal into the live ontology. "
            "Server revalidates gates and current ontology state; not a force/override. "
            "Requires ontology.apply. Pass proposal_id, request_id, idempotency_key "
            "(actor_key is injected by the MCP server).",
            lambda session, payload: OntologyMCPTools(session).apply_ontology_proposal(payload),
            request_model=ApplyProposalRequest,
        ),
        _payload_tool(
            "challenge_ontology_review",
            "Challenge a reject/reuse semantic review with new rationale/evidence.",
            lambda session, payload: OntologyMCPTools(session).challenge_ontology_review(payload),
            request_model=ChallengeOntologyReviewRequest,
        ),
        _payload_tool(
            "answer_semantic_clarification",
            "Answer an AtlasSynapse clarification_request_id. Required field is "
            "`response` (string) — not `answer`. Include semantic distinction and "
            "examples; triggers re-review.",
            lambda session, payload: OntologyMCPTools(session).answer_semantic_clarification(
                payload
            ),
            request_model=AnswerSemanticClarificationRequest,
        ),
        _payload_tool(
            "answer_identity_clarification",
            "Answer a write/identity clarification_request_id with chosen_entity_id, "
            "create_new, or reject. AtlasSynapse resumes the frozen operation; "
            "re-checks current prod state for staleness.",
            lambda session, payload: StatementMCPTools(session).answer_identity_clarification(
                payload
            ),
            request_model=AnswerIdentityClarificationRequest,
        ),
        _payload_tool(
            "report_feedback",
            "Report an agent feedback observation about system quality.",
            lambda session, payload: FeedbackMCPTools(session).report_feedback(payload),
        ),
    ]


def build_mcp_server(settings: Settings | None = None) -> StdioMCPServer:
    """Construct the stdio MCP server with thin adapters over domain services."""
    cfg = settings or get_settings()
    cfg.validate_production_secrets()
    configure_engine(cfg)
    return StdioMCPServer(
        name=cfg.app_name,
        instructions=(
            "AtlasSynapse semantic memory tools. The MCP server injects actor_key "
            f"('{cfg.mcp_actor_key}'); clients must not guess it. Mutations still need "
            "request_id and idempotency_key. Prefer dry_run=true to preview writes. "
            "Ontology propose ≠ apply: after READY_TO_APPLY, call apply_ontology_proposal "
            "to commit (server revalidates; no force). Predicate writes use domain_keys/"
            "range_keys; clarification answers use field `response`. Put disposable test "
            "ontology in namespace_key=smoke. Statement correction: prefer "
            "correct_statement (statement_id + temporal/object overrides) over "
            "assert+manual cleanup; supersede_statement uses previous_statement_id. "
            "Call get_runtime_config to see mcp_tools (includes correct_statement)."
        ),
        tools=build_mcp_tools(),
    )


def run_mcp_server(settings: Settings | None = None) -> None:
    """Run the configured MCP transport (stdio by default)."""
    cfg = settings or get_settings()
    if cfg.mcp_transport != "stdio":
        raise RuntimeError(
            f"MCP transport '{cfg.mcp_transport}' is not implemented; use MCP_TRANSPORT=stdio"
        )
    build_mcp_server(cfg).run()
