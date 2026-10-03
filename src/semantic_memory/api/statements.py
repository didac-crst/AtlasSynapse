"""HTTP endpoints for statement operations."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from semantic_memory.db import get_db_session
from semantic_memory.schemas.statements import (
    AssertStatementRequest,
    AssertStatementResponse,
    StatementResponse,
)
from semantic_memory.services.statements import StatementService

router = APIRouter(prefix="/v1", tags=["statements"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post("/statements", response_model=AssertStatementResponse)
def assert_statement(
    request: AssertStatementRequest, session: DbSession
) -> AssertStatementResponse:
    try:
        result = StatementService(session).assert_statement(request)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


@router.get("/statements/{statement_id}", response_model=StatementResponse)
def get_statement(statement_id: uuid.UUID, session: DbSession) -> StatementResponse:
    return StatementService(session).get(statement_id)
