"""Process entrypoints for API, migrations, and MCP transport."""

from __future__ import annotations

import sys


def run_api() -> None:
    """Serve the HTTP API (uvicorn factory)."""
    from semantic_memory.api.app import run

    run()


def run_migrate() -> None:
    """Apply Alembic migrations to head."""
    from alembic import command
    from alembic.config import Config

    from semantic_memory.config import get_settings

    cfg = get_settings()
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", cfg.sqlalchemy_database_uri)
    command.upgrade(alembic_cfg, "head")


def run_mcp() -> None:
    """Run the MCP stdio transport server."""
    from semantic_memory.mcp.server import run_mcp_server

    try:
        run_mcp_server()
    except KeyboardInterrupt:
        sys.exit(0)
