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
from semantic_memory.mcp.surfaces import (
    McpToolSurface,
    McpToolSurfaceMode,
    surface_visible,
)
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

# Keep a stable full-registry tuple for diagnostics; aliases included.
REGISTERED_TOOL_NAMES: tuple[str, ...] = ()  # filled after build_all_mcp_tools defined

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
    """Describes the configured MCP transport and visible tool names."""

    transport: str
    ready: bool = True
    message: str = (
        "Stdio MCP transport; tools/list filtered by MCP_TOOL_SURFACE "
        "(agent|advanced|admin|all). Visibility is UX only — capabilities still apply."
    )
    tools: tuple[str, ...] = field(default_factory=tuple)
    tool_surface: str = "agent"

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> MCPServerInfo:
        cfg = settings or get_settings()
        tools = tuple(tool.name for tool in build_mcp_tools(settings=cfg))
        return cls(
            transport=cfg.mcp_transport,
            tools=tools,
            tool_surface=cfg.mcp_tool_surface,
        )


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
    surface: McpToolSurface = McpToolSurface.ADVANCED,
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
        surface=surface,
    )


def build_all_mcp_tools() -> list[ToolSpec]:
    """Full MCP registry with surface tags (unfiltered)."""
    get_proposal_handler = lambda proposal_id: _call(  # noqa: E731
        lambda s: OntologyMCPTools(s).get_proposal(proposal_id)
    )
    get_proposal_schema = {
        "type": "object",
        "properties": {"proposal_id": {"type": "string"}},
        "required": ["proposal_id"],
    }
    return [
        ToolSpec(
            name="get_runtime_config",
            description=(
                "Return non-secret AtlasSynapse runtime config for deploy parity checks "
                "(semantic_review_mode, version, model thresholds, mcp_tool_surface)."
            ),
            handler=lambda: runtime_config(),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "create_entity",
            "Create a typed knowledge entity. Set dry_run=true to preview "
            "MATCH/CREATE/CLARIFY without persisting.",
            lambda session, payload: EntityMCPTools(session).create_entity(payload),
            surface=McpToolSurface.ADVANCED,
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
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "search_entities",
            "Search entities with ranking signals. Prefer search_memory for agent reads.",
            lambda session, payload: RetrievalMCPTools(session).search_entities(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        ToolSpec(
            name="get_entity_neighborhood",
            description=(
                "List neighboring entities (implementation-shaped). Prefer "
                "get_relevant_context for ordinary agent reads."
            ),
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
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "merge_entity",
            "Explicitly merge two entities. Copies source names/aliases onto the "
            "target so future create/search by those names reuse the survivor.",
            lambda session, payload: EntityMCPTools(session).merge_entity(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "add_entity_alias",
            "Add an alternate name/alias to an existing active entity "
            "(e.g. add 'Didac' to 'Didac Cristobal').",
            lambda session, payload: EntityMCPTools(session).add_entity_alias(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "assert_statement",
            "Store a fact. Pass subject_entity_id/object_entity_id when known, or "
            "subject/object EntityInput (canonical_name + class_key) and let the server "
            "MATCH/CREATE/CLARIFY. Prefer return_mode=standard (default) so the response "
            "describes the state transition. Set dry_run=true to preview.",
            lambda session, payload: StatementMCPTools(session).assert_statement(payload),
            surface=McpToolSurface.AGENT,
        ),
        ToolSpec(
            name="get_statement",
            description="Fetch a statement by id. Prefer mutation return_mode=standard.",
            handler=lambda statement_id: _call(
                lambda s: StatementMCPTools(s).get_statement(statement_id)
            ),
            input_schema={
                "type": "object",
                "properties": {"statement_id": {"type": "string"}},
                "required": ["statement_id"],
            },
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "search_statements",
            "Search statements with typed filters and stable pagination. Prefer "
            "search_memory / get_relevant_context for ordinary agent reads.",
            lambda session, payload: RetrievalMCPTools(session).search_statements(payload),
            request_model=SearchStatementsRequest,
            surface=McpToolSurface.ADVANCED,
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
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "ensure_source",
            "Ensure a reusable provenance source (create or reuse by identity).",
            lambda session, payload: StatementMCPTools(session).ensure_source(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "ingest_source_content",
            "Ingest source content. Prefer content + content_format (html|markdown|text); "
            "AtlasSynapse preserves the exact original and deterministically canonicalizes "
            "(HTML→Markdown v1). Optional canonical_format defaults to markdown.",
            lambda session, payload: StatementMCPTools(session).ingest_source_content(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "get_source_content",
            "Fetch latest or specific source content revision by source_id, "
            "document_entity_id, or revision_id.",
            lambda session, payload: StatementMCPTools(session).get_source_content(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "search_source_content",
            "Lexical search over canonical source content; returns passages with "
            "revision-qualified locators.",
            lambda session, payload: StatementMCPTools(session).search_source_content(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "add_evidence",
            "Attach evidence to a statement.",
            lambda session, payload: StatementMCPTools(session).add_evidence(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "supersede_statement",
            "Low-level supersession by previous_statement_id. Prefer correct_statement "
            "for ordinary corrections (user intent, not storage semantics).",
            lambda session, payload: StatementMCPTools(session).supersede_statement(payload),
            request_model=SupersedeStatementRequest,
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "correct_statement",
            "Correct temporal validity or object of a fact (immutable history via "
            "supersession). Prefer this over assert+manual cleanup or supersede_statement.",
            lambda session, payload: StatementMCPTools(session).correct_statement(payload),
            request_model=CorrectStatementRequest,
            surface=McpToolSurface.AGENT,
        ),
        _payload_tool(
            "retract_statement",
            "Retract a fact (soft; does not delete). Idempotent. Optional reason stored "
            "in statement metadata.",
            lambda session, payload: StatementMCPTools(session).retract_statement(payload),
            request_model=RetractStatementRequest,
            surface=McpToolSurface.AGENT,
        ),
        ToolSpec(
            name="get_timeline",
            description="Get an entity's statement timeline ordered by time.",
            handler=lambda entity_id: _call(lambda s: StatementMCPTools(s).get_timeline(entity_id)),
            input_schema={
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "required": ["entity_id"],
            },
            surface=McpToolSurface.AGENT,
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
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "assert_batch",
            "Atomically assert a batch of statements. Prefer assert_statement for "
            "ordinary single-fact writes.",
            lambda session, payload: StatementMCPTools(session).assert_batch(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "search_memory",
            "Search memory (entities + statements). Prefer this for ordinary agent lookup.",
            lambda session, payload: RetrievalMCPTools(session).search_semantic_memory(payload),
            surface=McpToolSurface.AGENT,
        ),
        _payload_tool(
            "search_semantic_memory",
            "Search semantic memory (advanced alias of search_memory).",
            lambda session, payload: RetrievalMCPTools(session).search_semantic_memory(payload),
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "get_relevant_context",
            "Compose the relevant memory context for an entity (preferred agent read).",
            lambda session, payload: RetrievalMCPTools(session).get_relevant_context(payload),
            surface=McpToolSurface.AGENT,
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
            surface=McpToolSurface.ADVANCED,
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
            surface=McpToolSurface.ADVANCED,
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
            surface=McpToolSurface.ADVANCED,
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
            surface=McpToolSurface.ADVANCED,
        ),
        ToolSpec(
            name="get_ontology_proposal",
            description="Fetch an ontology proposal by id.",
            handler=get_proposal_handler,
            input_schema=get_proposal_schema,
            surface=McpToolSurface.AGENT,
        ),
        ToolSpec(
            name="get_proposal",
            description=(
                "Fetch an ontology proposal by id (advanced alias of get_ontology_proposal)."
            ),
            handler=get_proposal_handler,
            input_schema=get_proposal_schema,
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "propose_class",
            "Propose a new ontology class. On ambiguous overlap, response includes "
            "open_clarification_request. Use namespace_key='smoke' for disposable tests.",
            lambda session, payload: OntologyMCPTools(session).propose_class(payload),
            request_model=ProposeClassRequest,
            surface=McpToolSurface.AGENT,
        ),
        _payload_tool(
            "propose_predicate",
            "Propose a new ontology predicate. Write fields are domain_keys/range_keys. "
            "Use namespace_key='smoke' for disposable tests.",
            lambda session, payload: OntologyMCPTools(session).propose_predicate(payload),
            request_model=ProposePredicateRequest,
            surface=McpToolSurface.AGENT,
        ),
        _payload_tool(
            "propose_constraint",
            "Propose an ontology constraint.",
            lambda session, payload: OntologyMCPTools(session).propose_constraint(payload),
            request_model=ProposeConstraintRequest,
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "propose_alias",
            "Propose an ontology alias.",
            lambda session, payload: OntologyMCPTools(session).propose_alias(payload),
            request_model=ProposeAliasRequest,
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "propose_class_parent",
            "Propose a class parent link.",
            lambda session, payload: OntologyMCPTools(session).propose_class_parent(payload),
            request_model=ProposeClassParentRequest,
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "apply_ontology_proposal",
            "Commit an applyable ontology proposal into the live ontology. "
            "Server revalidates gates; not a force/override. Requires ontology.apply.",
            lambda session, payload: OntologyMCPTools(session).apply_ontology_proposal(payload),
            request_model=ApplyProposalRequest,
            surface=McpToolSurface.AGENT,
        ),
        _payload_tool(
            "challenge_ontology_review",
            "Challenge a reject/reuse semantic review with new rationale/evidence.",
            lambda session, payload: OntologyMCPTools(session).challenge_ontology_review(payload),
            request_model=ChallengeOntologyReviewRequest,
            surface=McpToolSurface.ADVANCED,
        ),
        _payload_tool(
            "answer_semantic_clarification",
            "Answer an ontology semantic clarification_request_id. Required field is "
            "`response` (string). Include semantic distinction and examples.",
            lambda session, payload: OntologyMCPTools(session).answer_semantic_clarification(
                payload
            ),
            request_model=AnswerSemanticClarificationRequest,
            surface=McpToolSurface.AGENT,
        ),
        _payload_tool(
            "answer_identity_clarification",
            "Answer a write/identity clarification with chosen_entity_id, create_new, "
            "or reject. AtlasSynapse resumes the frozen operation.",
            lambda session, payload: StatementMCPTools(session).answer_identity_clarification(
                payload
            ),
            request_model=AnswerIdentityClarificationRequest,
            surface=McpToolSurface.AGENT,
        ),
        _payload_tool(
            "report_feedback",
            "Report an agent feedback observation about system quality.",
            lambda session, payload: FeedbackMCPTools(session).report_feedback(payload),
            surface=McpToolSurface.ADVANCED,
        ),
    ]


def build_mcp_tools(settings: Settings | None = None) -> list[ToolSpec]:
    """Tools visible under the configured MCP_TOOL_SURFACE."""
    cfg = settings or get_settings()
    mode = McpToolSurfaceMode(cfg.mcp_tool_surface)
    return [tool for tool in build_all_mcp_tools() if surface_visible(mode, tool.surface)]


def _agent_instructions(cfg: Settings) -> str:
    if cfg.mcp_tool_surface == "agent":
        return (
            "AtlasSynapse agent memory tools (MCP_TOOL_SURFACE=agent). "
            f"Actor '{cfg.mcp_actor_key}' is injected — do not supply actor_key. "
            "Prefer search_memory / get_relevant_context for reads; "
            "assert_statement / correct_statement / retract_statement for writes "
            "(return_mode=standard describes the state transition). "
            "Use answer_identity_clarification when outcome=CLARIFY. "
            "Ontology: propose_class or propose_predicate, then apply_ontology_proposal "
            "when READY_TO_APPLY. Mutations need request_id + idempotency_key; "
            "prefer dry_run=true to preview."
        )
    return (
        "AtlasSynapse semantic memory tools. The MCP server injects actor_key "
        f"('{cfg.mcp_actor_key}'); clients must not guess it. Mutations still need "
        "request_id and idempotency_key. Prefer dry_run=true to preview writes. "
        "Ontology propose ≠ apply: after READY_TO_APPLY, call apply_ontology_proposal "
        "to commit (server revalidates; no force). Prefer correct_statement over "
        "supersede_statement for ordinary corrections. "
        f"mcp_tool_surface={cfg.mcp_tool_surface}."
    )


def build_mcp_server(settings: Settings | None = None) -> StdioMCPServer:
    """Construct the stdio MCP server with thin adapters over domain services."""
    cfg = settings or get_settings()
    cfg.validate_production_secrets()
    configure_engine(cfg)
    return StdioMCPServer(
        name=cfg.app_name,
        instructions=_agent_instructions(cfg),
        tools=build_mcp_tools(settings=cfg),
        max_inflight=cfg.mcp_max_inflight,
    )


REGISTERED_TOOL_NAMES = tuple(tool.name for tool in build_all_mcp_tools())


def run_mcp_server(settings: Settings | None = None) -> None:
    """Run the configured MCP transport (stdio by default)."""
    cfg = settings or get_settings()
    if cfg.mcp_transport != "stdio":
        raise RuntimeError(
            f"MCP transport '{cfg.mcp_transport}' is not implemented; use MCP_TRANSPORT=stdio"
        )
    build_mcp_server(cfg).run()
