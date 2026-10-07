"""MCP tool surface classification — visibility UX, not authorization."""

from __future__ import annotations

from enum import StrEnum


class McpToolSurface(StrEnum):
    """Which catalog a tool belongs to.

    Visibility is independent of capability checks: hiding a tool does not
    replace ontology.apply / knowledge.write / audit gates.
    """

    AGENT = "agent"
    ADVANCED = "advanced"
    ADMIN = "admin"


class McpToolSurfaceMode(StrEnum):
    """What the server advertises via tools/list (and will dispatch)."""

    AGENT = "agent"
    ADVANCED = "advanced"
    ADMIN = "admin"
    ALL = "all"


_SURFACE_RANK: dict[McpToolSurface, int] = {
    McpToolSurface.AGENT: 0,
    McpToolSurface.ADVANCED: 1,
    McpToolSurface.ADMIN: 2,
}


def surface_visible(mode: McpToolSurfaceMode | str, tool_surface: McpToolSurface | str) -> bool:
    """Return True if a tool on ``tool_surface`` is advertised under ``mode``."""
    mode_value = McpToolSurfaceMode(mode)
    tool = McpToolSurface(tool_surface)
    if mode_value == McpToolSurfaceMode.ALL:
        return True
    mode_as_surface = McpToolSurface(mode_value.value)
    return _SURFACE_RANK[tool] <= _SURFACE_RANK[mode_as_surface]


# Default agent catalog (names after aliases). Used for docs/tests.
AGENT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "search_memory",
        "get_relevant_context",
        "get_timeline",
        "assert_statement",
        "correct_statement",
        "retract_statement",
        "answer_identity_clarification",
        "propose_class",
        "propose_predicate",
        "get_ontology_proposal",
        "apply_ontology_proposal",
        "answer_semantic_clarification",
    }
)
