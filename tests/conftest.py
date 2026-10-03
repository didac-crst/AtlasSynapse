"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from semantic_memory.api.app import create_app
from semantic_memory.config import Settings, get_settings
from semantic_memory.db import get_db_session, reset_engine


def pytest_configure() -> None:
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://semantic_memory:semantic_memory@localhost:5432/semantic_memory",
    )
    # Production defaults fail closed; tests explicitly enable a local admin token.
    os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _database_url())
    return cfg


@pytest.fixture(scope="session")
def engine(alembic_cfg: Config) -> Generator[Engine, None, None]:
    get_settings.cache_clear()
    reset_engine()
    eng = create_engine(_database_url(), pool_pre_ping=True, future=True)
    command.upgrade(alembic_cfg, "head")
    yield eng
    eng.dispose()
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session, None, None]:
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess: Session, trans: object) -> None:
        if getattr(trans, "nested", False) and not getattr(
            getattr(trans, "_parent", None), "nested", True
        ):
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def client(engine: Engine) -> Generator[TestClient, None, None]:
    get_settings.cache_clear()
    reset_engine()
    settings = Settings()
    app = create_app(settings)

    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)

    def _override_db() -> Generator[Session, None, None]:
        session = SessionLocal()
        session.begin_nested()

        @event.listens_for(session, "after_transaction_end")
        def _restart_savepoint(sess: Session, trans: object) -> None:
            if getattr(trans, "nested", False) and not getattr(
                getattr(trans, "_parent", None), "nested", True
            ):
                sess.begin_nested()

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
