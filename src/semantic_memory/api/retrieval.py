"""HTTP endpoints for Phase 13 retrieval."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from semantic_memory.db import get_db_session
from semantic_memory.models.enums import EntityStatus, StatementStatus
from semantic_memory.schemas.retrieval import (
    NeighborhoodResponse,
    RelevantContextRequest,
    RelevantContextResponse,
    SearchEntitiesRequest,
    SearchEntitiesResponse,
    SearchSemanticMemoryRequest,
    SearchSemanticMemoryResponse,
    SearchStatementsRequest,
    SearchStatementsResponse,
)
from semantic_memory.services.retrieval import RetrievalService

router = APIRouter(prefix="/v1", tags=["retrieval"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.get("/entities/search", response_model=SearchEntitiesResponse)
def search_entities(
    session: DbSession,
    query: str = Query(min_length=1),
    class_key: str | None = None,
    namespace_key: str = "core",
    status: EntityStatus | None = EntityStatus.ACTIVE,
    limit: int = Query(default=25, ge=1, le=100),
    as_of: datetime | None = None,
) -> SearchEntitiesResponse:
    return RetrievalService(session).search_entities(
        SearchEntitiesRequest(
            query=query,
            class_key=class_key,
            namespace_key=namespace_key,
            status=status,
            limit=limit,
            as_of=as_of,
        )
    )


@router.get("/statements/search", response_model=SearchStatementsResponse)
def search_statements(
    session: DbSession,
    query: str | None = None,
    entity_id: uuid.UUID | None = None,
    predicate_key: str | None = None,
    namespace_key: str = "core",
    status: StatementStatus | None = StatementStatus.ASSERTED,
    limit: int = Query(default=25, ge=1, le=100),
    as_of: datetime | None = None,
) -> SearchStatementsResponse:
    return RetrievalService(session).search_statements(
        SearchStatementsRequest(
            query=query,
            entity_id=entity_id,
            predicate_key=predicate_key,
            namespace_key=namespace_key,
            status=status,
            limit=limit,
            as_of=as_of,
        )
    )


@router.get("/entities/{entity_id}/neighborhood", response_model=NeighborhoodResponse)
def get_entity_neighborhood(
    entity_id: uuid.UUID,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    as_of: datetime | None = None,
) -> NeighborhoodResponse:
    return RetrievalService(session).get_entity_neighborhood(entity_id, limit=limit, as_of=as_of)


@router.get("/memory/search", response_model=SearchSemanticMemoryResponse)
def search_semantic_memory(
    session: DbSession,
    query: str = Query(min_length=1),
    entity_id: uuid.UUID | None = None,
    class_key: str | None = None,
    namespace_key: str = "core",
    include_conflicts: bool = True,
    limit: int = Query(default=25, ge=1, le=100),
    as_of: datetime | None = None,
) -> SearchSemanticMemoryResponse:
    return RetrievalService(session).search_semantic_memory(
        SearchSemanticMemoryRequest(
            query=query,
            entity_id=entity_id,
            class_key=class_key,
            namespace_key=namespace_key,
            include_conflicts=include_conflicts,
            limit=limit,
            as_of=as_of,
        )
    )


@router.post("/memory/relevant-context", response_model=RelevantContextResponse)
def get_relevant_context(
    request: RelevantContextRequest, session: DbSession
) -> RelevantContextResponse:
    return RetrievalService(session).get_relevant_context(request)
