"""HTTP endpoints for provenance operations."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.schemas.provenance import (
    AddEvidenceRequest,
    AddEvidenceResponse,
    EnsureSourceRequest,
    EnsureSourceResponse,
    GetSourceContentRequest,
    GetSourceContentResponse,
    IngestSourceContentRequest,
    IngestSourceContentResponse,
    SearchSourceContentRequest,
    SearchSourceContentResponse,
)
from semantic_memory.services.provenance import ProvenanceService

router = APIRouter(prefix="/v1", tags=["provenance"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post("/sources/ensure", response_model=EnsureSourceResponse)
def ensure_source(request: EnsureSourceRequest, session: DbSession) -> EnsureSourceResponse:
    return run_audited_mutation(session, lambda: ProvenanceService(session).ensure_source(request))


@router.post("/sources/content", response_model=IngestSourceContentResponse)
def ingest_source_content(
    request: IngestSourceContentRequest, session: DbSession
) -> IngestSourceContentResponse:
    return run_audited_mutation(
        session, lambda: ProvenanceService(session).ingest_source_content(request)
    )


@router.get("/sources/content", response_model=GetSourceContentResponse)
def get_source_content(
    session: DbSession,
    source_id: Annotated[uuid.UUID | None, Query()] = None,
    document_entity_id: Annotated[uuid.UUID | None, Query()] = None,
    revision_id: Annotated[uuid.UUID | None, Query()] = None,
    include_original: Annotated[bool, Query()] = True,
) -> GetSourceContentResponse:
    request = GetSourceContentRequest(
        source_id=source_id,
        document_entity_id=document_entity_id,
        revision_id=revision_id,
        include_original=include_original,
    )
    return ProvenanceService(session).get_source_content(request)


@router.get("/sources/content/search", response_model=SearchSourceContentResponse)
def search_source_content(
    session: DbSession,
    query: Annotated[str, Query(min_length=1)],
    source_id: Annotated[uuid.UUID | None, Query()] = None,
    document_entity_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    context_chars: Annotated[int, Query(ge=0, le=2000)] = 120,
) -> SearchSourceContentResponse:
    request = SearchSourceContentRequest(
        query=query,
        source_id=source_id,
        document_entity_id=document_entity_id,
        limit=limit,
        context_chars=context_chars,
    )
    return ProvenanceService(session).search_source_content(request)


@router.post("/evidence", response_model=AddEvidenceResponse)
def add_evidence(request: AddEvidenceRequest, session: DbSession) -> AddEvidenceResponse:
    return run_audited_mutation(session, lambda: ProvenanceService(session).add_evidence(request))
