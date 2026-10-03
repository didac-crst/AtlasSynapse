"""Health endpoint tests."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from semantic_memory.api.app import create_app
from semantic_memory.config import Settings, get_settings
from semantic_memory.db import get_db_session, reset_engine


def _alembic_head_revision() -> str:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    assert len(heads) == 1
    return heads[0]


def test_health_live(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["migrations"] == _alembic_head_revision()


def test_health_ready_returns_503_when_migrations_missing(engine: Engine) -> None:
    get_settings.cache_clear()
    reset_engine()
    settings = Settings()
    app = create_app(settings)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE alembic_version RENAME TO alembic_version_backup"))

    def _override_db():  # type: ignore[no-untyped-def]
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _override_db
    try:
        with TestClient(app) as test_client:
            response = test_client.get("/health/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["database"] == "ok"
        assert body["migrations"] == "missing"
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE alembic_version_backup RENAME TO alembic_version"))
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        reset_engine()
