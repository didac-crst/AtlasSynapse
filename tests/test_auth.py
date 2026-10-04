"""HTTP API authentication boundary tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from semantic_memory.api.app import create_app
from semantic_memory.config import Settings, get_settings
from semantic_memory.db import get_db_session, reset_engine


@pytest.fixture
def authed_client(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    get_settings.cache_clear()
    reset_engine()
    monkeypatch.setenv("HTTP_API_TOKEN", "secret-api-token")
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")
    monkeypatch.setenv("APP_ENV", "development")
    settings = Settings()
    app = create_app(settings)

    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)

    def _override_db() -> Generator[Session, None, None]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    if transaction.is_active:
        transaction.rollback()
    connection.close()
    get_settings.cache_clear()
    reset_engine()


def test_http_api_token_required_when_configured(authed_client: TestClient) -> None:
    denied = authed_client.get("/v1/ontology/classes", params={"class_key": "Person"})
    assert denied.status_code == 401
    assert denied.json()["error_code"] == "UNAUTHORIZED"

    live = authed_client.get("/health/live")
    assert live.status_code == 200

    ok = authed_client.get(
        "/v1/ontology/classes",
        params={"class_key": "Person"},
        headers={"Authorization": "Bearer secret-api-token"},
    )
    assert ok.status_code == 200
    assert ok.json()["key"] == "Person"

    ok_header = authed_client.get(
        "/v1/ontology/classes",
        params={"class_key": "Person"},
        headers={"X-API-Token": "secret-api-token"},
    )
    assert ok_header.status_code == 200


def test_production_requires_secrets() -> None:
    with pytest.raises(ValueError, match="HTTP_API_TOKEN"):
        Settings(
            app_env="production",
            http_api_token="",
            admin_api_token="admin",
            database_url="postgresql+psycopg://prod:prod@db:5432/semantic_memory",
        ).validate_production_secrets()

    with pytest.raises(ValueError, match="development default credentials"):
        Settings(
            app_env="production",
            http_api_token="api",
            admin_api_token="admin",
            database_url=(
                "postgresql+psycopg://semantic_memory:semantic_memory@db:5432/semantic_memory"
            ),
        ).validate_production_secrets()

    Settings(
        app_env="production",
        http_api_token="api",
        admin_api_token="admin",
        database_url="postgresql+psycopg://prod:prod@db:5432/semantic_memory",
    ).validate_production_secrets()
