"""SQLAlchemy engine and session wiring."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from semantic_memory.config import Settings, get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Create a SQLAlchemy engine from settings."""
    cfg = settings or get_settings()
    return create_engine(
        cfg.sqlalchemy_database_uri,
        pool_size=cfg.database_pool_size,
        max_overflow=cfg.database_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


def configure_engine(settings: Settings | None = None) -> Engine:
    """Configure the process-wide engine and session factory."""
    global _engine, _SessionLocal
    _engine = create_db_engine(settings)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_engine() -> Engine:
    """Return the configured engine, creating it on first use."""
    if _engine is None:
        return configure_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the configured session factory."""
    if _SessionLocal is None:
        configure_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def check_database_connectivity(session: Session) -> bool:
    """Return True when the database accepts a simple connectivity probe."""
    session.execute(text("SELECT 1"))
    return True


def reset_engine() -> None:
    """Dispose the process-wide engine. Intended for tests."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
