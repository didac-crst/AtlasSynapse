"""Shared runtime/config diagnostics for API and MCP."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from semantic_memory.config import Settings, get_settings


def package_version() -> str:
    try:
        return version("atlas-synapse")
    except PackageNotFoundError:
        return "0.1.0.dev6"


def runtime_config(settings: Settings | None = None) -> dict[str, Any]:
    """Return non-secret deployment diagnostics shared by API and MCP."""
    # Import locally to avoid import cycles with mcp.server → runtime_info.
    from semantic_memory.mcp.server import REGISTERED_TOOL_NAMES, build_mcp_tools

    cfg = settings or get_settings()
    visible = [tool.name for tool in build_mcp_tools(settings=cfg)]
    return {
        "app_name": cfg.app_name,
        "app_env": cfg.app_env,
        "version": package_version(),
        "semantic_review_mode": cfg.semantic_review_mode,
        "semantic_review_provider": cfg.semantic_review_provider,
        "semantic_review_model": cfg.semantic_review_model,
        "semantic_review_approve_threshold": cfg.semantic_review_approve_threshold,
        "semantic_review_reject_threshold": cfg.semantic_review_reject_threshold,
        "embedding_mode": cfg.embedding_mode,
        "mcp_transport": cfg.mcp_transport,
        "mcp_max_inflight": cfg.mcp_max_inflight,
        "mcp_tool_surface": cfg.mcp_tool_surface,
        "mcp_tool_count": len(visible),
        "mcp_tools": visible,
        "mcp_tool_count_all": len(REGISTERED_TOOL_NAMES),
        "mcp_tools_all": list(REGISTERED_TOOL_NAMES),
    }
