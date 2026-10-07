"""Shared runtime/config diagnostics for API and MCP."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from semantic_memory.config import Settings, get_settings


def package_version() -> str:
    try:
        return version("atlas-synapse")
    except PackageNotFoundError:
        return "0.1.0.dev5"


def runtime_config(settings: Settings | None = None) -> dict[str, Any]:
    """Return non-secret deployment diagnostics shared by API and MCP."""
    # Import locally to avoid import cycles with mcp.server → runtime_info.
    from semantic_memory.mcp.server import REGISTERED_TOOL_NAMES

    cfg = settings or get_settings()
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
        "mcp_tool_count": len(REGISTERED_TOOL_NAMES),
        "mcp_tools": list(REGISTERED_TOOL_NAMES),
    }
