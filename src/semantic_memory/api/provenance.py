"""HTTP endpoints for provenance operations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.schemas.provenance import (
    AddEvidenceRequest,
    AddEvidenceResponse,
    EnsureSourceRequest,
    EnsureSourceResponse,
)
from semantic_memory.services.provenance import ProvenanceService

router = APIRouter(prefix="/v1", tags=["provenance"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post("/sources/ensure", response_model=EnsureSourceResponse)
def ensure_source(request: EnsureSourceRequest, session: DbSession) -> EnsureSourceResponse:
    return run_audited_mutation(session, lambda: ProvenanceService(session).ensure_source(request))


@router.post("/evidence", response_model=AddEvidenceResponse)
def add_evidence(request: AddEvidenceRequest, session: DbSession) -> AddEvidenceResponse:
    return run_audited_mutation(session, lambda: ProvenanceService(session).add_evidence(request))
