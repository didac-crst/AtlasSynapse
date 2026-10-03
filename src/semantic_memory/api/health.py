"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from semantic_memory.db import get_db_session
from semantic_memory.schemas.health import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])

DbSession = Annotated[Session, Depends(get_db_session)]


def _check_migrations(session: Session) -> tuple[bool, str]:
    """Verify Alembic migration compatibility."""
    try:
        row = session.execute(text("SELECT version_num FROM alembic_version")).first()
    except Exception:
        return False, "missing"
    if row is None:
        return False, "missing"
    return True, str(row[0])


@router.get("/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    """Process liveness probe."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadyResponse)
def ready(response: Response, session: DbSession) -> ReadyResponse:
    """Readiness probe for database connectivity and migrations."""
    database_status = "ok"
    migrations_ok = False
    migrations_status = "unknown"

    try:
        session.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(
            status="unavailable",
            database=database_status,
            migrations="unknown",
        )

    migrations_ok, migrations_status = _check_migrations(session)
    if not migrations_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(
            status="unavailable",
            database=database_status,
            migrations=migrations_status,
        )

    return ReadyResponse(
        status="ok",
        database=database_status,
        migrations=migrations_status,
    )
