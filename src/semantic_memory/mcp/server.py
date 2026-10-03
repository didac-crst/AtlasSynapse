"""Thin MCP transport placeholder with entity tool registration."""

from __future__ import annotations

from dataclasses import dataclass, field

from semantic_memory.config import Settings, get_settings


@dataclass(frozen=True)
class MCPPlaceholder:
    """Placeholder describing the configured MCP transport and Phase 2 tools."""

    transport: str
    ready: bool = True
    message: str = "Phase 2 exposes create_entity and get_entity tool adapters."
    tools: tuple[str, ...] = field(default_factory=lambda: ("create_entity", "get_entity"))

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> MCPPlaceholder:
        cfg = settings or get_settings()
        return cls(transport=cfg.mcp_transport)
