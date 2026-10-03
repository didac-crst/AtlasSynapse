"""Thin MCP transport placeholder.

Phase 0/1 keeps MCP as a typed boundary stub. Tool implementations arrive with
later knowledge-plane and ontology phases.
"""

from dataclasses import dataclass

from semantic_memory.config import Settings, get_settings


@dataclass(frozen=True)
class MCPPlaceholder:
    """Placeholder describing the configured MCP transport."""

    transport: str
    ready: bool = False
    message: str = "MCP tools are not implemented in Phase 0/1."

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "MCPPlaceholder":
        cfg = settings or get_settings()
        return cls(transport=cfg.mcp_transport)
