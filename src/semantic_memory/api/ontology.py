"""HTTP endpoints for the ontology read plane."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from semantic_memory.db import get_db_session
from semantic_memory.schemas.ontology import (
    OntologyClassResponse,
    OntologyContextResponse,
    OntologyPredicateResponse,
    OntologySearchResponse,
)
from semantic_memory.services.ontology import OntologyService

router = APIRouter(prefix="/v1/ontology", tags=["ontology"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.get("/classes", response_model=OntologyClassResponse)
def get_class(
    session: DbSession,
    class_key: str | None = None,
    class_id: uuid.UUID | None = None,
    alias: str | None = None,
    namespace_key: str = "core",
) -> OntologyClassResponse:
    return OntologyService(session).get_class(
        class_key=class_key,
        class_id=class_id,
        alias=alias,
        namespace_key=namespace_key,
    )


@router.get("/predicates", response_model=OntologyPredicateResponse)
def get_predicate(
    session: DbSession,
    predicate_key: str | None = None,
    predicate_id: uuid.UUID | None = None,
    alias: str | None = None,
    namespace_key: str = "core",
) -> OntologyPredicateResponse:
    return OntologyService(session).get_predicate(
        predicate_key=predicate_key,
        predicate_id=predicate_id,
        alias=alias,
        namespace_key=namespace_key,
    )


@router.get("/search", response_model=OntologySearchResponse)
def search_ontology(
    session: DbSession,
    query: str = Query(min_length=1),
    namespace_key: str | None = "core",
    limit: int = Query(default=25, ge=1, le=100),
) -> OntologySearchResponse:
    return OntologyService(session).search_ontology(
        query=query,
        namespace_key=namespace_key,
        limit=limit,
    )


@router.get("/context", response_model=OntologyContextResponse)
def get_ontology_context(
    session: DbSession,
    class_key: str | None = None,
    class_id: uuid.UUID | None = None,
    alias: str | None = None,
    namespace_key: str = "core",
) -> OntologyContextResponse:
    return OntologyService(session).get_ontology_context(
        class_key=class_key,
        class_id=class_id,
        alias=alias,
        namespace_key=namespace_key,
    )
