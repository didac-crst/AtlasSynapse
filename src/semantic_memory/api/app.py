"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from semantic_memory import __version__
from semantic_memory.api.actors import router as actors_router
from semantic_memory.api.conflicts import router as conflicts_router
from semantic_memory.api.entities import router as entities_router
from semantic_memory.api.errors import register_exception_handlers
from semantic_memory.api.health import router as health_router
from semantic_memory.api.provenance import router as provenance_router
from semantic_memory.api.statements import router as statements_router
from semantic_memory.config import Settings, get_settings
from semantic_memory.db import configure_engine
from semantic_memory.observability.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = settings or get_settings()
    configure_logging(cfg.log_level)
    configure_engine(cfg)

    app = FastAPI(
        title=cfg.app_name,
        version=__version__,
        docs_url="/docs" if cfg.app_env != "production" else None,
        redoc_url=None,
    )
    app.state.settings = cfg
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(actors_router)
    app.include_router(entities_router)
    app.include_router(statements_router)
    app.include_router(provenance_router)
    app.include_router(conflicts_router)
    return app


def run() -> None:
    """CLI entrypoint for local HTTP serving."""
    import uvicorn

    cfg = get_settings()
    uvicorn.run(
        "semantic_memory.api.app:create_app",
        factory=True,
        host=cfg.http_host,
        port=cfg.http_port,
        reload=cfg.app_env == "development",
    )
