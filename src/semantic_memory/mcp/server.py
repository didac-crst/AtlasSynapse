"""Thin MCP transport placeholder with registered knowledge-plane tools."""

from __future__ import annotations

from dataclasses import dataclass, field

from semantic_memory.config import Settings, get_settings


@dataclass(frozen=True)
class MCPPlaceholder:
    """Placeholder describing the configured MCP transport and registered tools."""

    transport: str
    ready: bool = True
    message: str = (
        "Exposes entity, statement, provenance, conflict, merge, "
        "supersession, retraction, timeline, retrieval, ontology read, and proposal tools."
    )
    tools: tuple[str, ...] = field(
        default_factory=lambda: (
            "create_entity",
            "get_entity",
            "search_entities",
            "get_entity_neighborhood",
            "merge_entity",
            "assert_statement",
            "get_statement",
            "search_statements",
            "explain_statement",
            "add_evidence",
            "supersede_statement",
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
        )
    )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> MCPPlaceholder:
        cfg = settings or get_settings()
        return cls(transport=cfg.mcp_transport)
