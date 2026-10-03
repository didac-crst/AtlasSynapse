"""Thin MCP transport placeholder with Phase 3 tool registration."""

from __future__ import annotations

from dataclasses import dataclass, field

from semantic_memory.config import Settings, get_settings


@dataclass(frozen=True)
class MCPPlaceholder:
    """Placeholder describing the configured MCP transport and registered tools."""

    transport: str
    ready: bool = True
    message: str = (
        "Phase 3 exposes create_entity, get_entity, assert_statement, and get_statement tools."
    )
    tools: tuple[str, ...] = field(
        default_factory=lambda: (
            "create_entity",
            "get_entity",
            "assert_statement",
            "get_statement",
        )
    )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> MCPPlaceholder:
        cfg = settings or get_settings()
        return cls(transport=cfg.mcp_transport)
