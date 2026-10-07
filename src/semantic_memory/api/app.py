"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from semantic_memory import __version__
from semantic_memory.api.actors import router as actors_router
from semantic_memory.api.admin import router as admin_router
from semantic_memory.api.auth import HttpApiTokenMiddleware
from semantic_memory.api.batches import router as batches_router
from semantic_memory.api.conflicts import router as conflicts_router
from semantic_memory.api.entities import router as entities_router
from semantic_memory.api.errors import register_exception_handlers
from semantic_memory.api.feedback import router as feedback_router
from semantic_memory.api.health import router as health_router
from semantic_memory.api.ontology import router as ontology_router
from semantic_memory.api.proposals import router as proposals_router
from semantic_memory.api.provenance import router as provenance_router
from semantic_memory.api.retrieval import router as retrieval_router
from semantic_memory.api.statements import router as statements_router
from semantic_memory.api.timing_middleware import ReadTimingMiddleware
from semantic_memory.config import Settings, get_settings
from semantic_memory.db import configure_engine
from semantic_memory.observability.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = settings or get_settings()
    cfg.validate_production_secrets()
    configure_logging(cfg.log_level)
    configure_engine(cfg)

    app = FastAPI(
        title=cfg.app_name,
        version=__version__,
        docs_url="/docs" if cfg.app_env != "production" else None,
        redoc_url=None,
    )
    app.state.settings = cfg
    # Outer middleware runs first on the way in; timing wraps auth + handlers.
    app.add_middleware(ReadTimingMiddleware)
    app.add_middleware(HttpApiTokenMiddleware, settings=cfg)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(actors_router)
    # Retrieval static paths (/entities/search, /statements/search) before UUID routes.
    app.include_router(retrieval_router)
    app.include_router(entities_router)
    app.include_router(statements_router)
    app.include_router(provenance_router)
    app.include_router(conflicts_router)
    app.include_router(batches_router)
    app.include_router(ontology_router)
    app.include_router(proposals_router)
    app.include_router(feedback_router)
    app.include_router(admin_router)
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
