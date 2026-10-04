"""Thin MCP transport adapter."""

from semantic_memory.mcp.server import (
    MCPPlaceholder,
    MCPServerInfo,
    build_mcp_server,
    run_mcp_server,
)
from semantic_memory.mcp.tools import EntityMCPTools, StatementMCPTools

__all__ = [
    "EntityMCPTools",
    "MCPPlaceholder",
    "MCPServerInfo",
    "StatementMCPTools",
    "build_mcp_server",
    "run_mcp_server",
]
