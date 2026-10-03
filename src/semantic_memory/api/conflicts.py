"""HTTP endpoints for conflict retrieval and resolution metadata."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.models.enums import ConflictStatus
from semantic_memory.schemas.conflicts import (
    ConflictMutationResponse,
    DismissConflictRequest,
    FindConflictsResponse,
    ResolveConflictRequest,
)
from semantic_memory.services.conflicts import ConflictService

router = APIRouter(prefix="/v1", tags=["conflicts"])
DbSession = Annotated[Session, Depends(get_db_session)]
ConflictStatusQuery = Annotated[ConflictStatus | None, Query()]


@router.get("/conflicts", response_model=FindConflictsResponse)
def find_conflicts(
    session: DbSession,
    entity_id: uuid.UUID | None = None,
    statement_id: uuid.UUID | None = None,
    status: ConflictStatusQuery = ConflictStatus.OPEN,
) -> FindConflictsResponse:
    return ConflictService(session).find_conflicts(
        entity_id=entity_id,
        statement_id=statement_id,
        status=status,
    )


@router.post("/conflicts/resolve", response_model=ConflictMutationResponse)
def resolve_conflict(
    request: ResolveConflictRequest, session: DbSession
) -> ConflictMutationResponse:
    return run_audited_mutation(session, lambda: ConflictService(session).resolve_conflict(request))


@router.post("/conflicts/dismiss", response_model=ConflictMutationResponse)
def dismiss_conflict(
    request: DismissConflictRequest, session: DbSession
) -> ConflictMutationResponse:
    return run_audited_mutation(session, lambda: ConflictService(session).dismiss_conflict(request))
