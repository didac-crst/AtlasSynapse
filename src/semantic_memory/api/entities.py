"""HTTP endpoints for entity operations."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.schemas.entities import (
    CreateEntityRequest,
    CreateEntityResponse,
    EntityResponse,
)
from semantic_memory.services.entities import EntityService

router = APIRouter(prefix="/v1", tags=["entities"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post("/entities", response_model=CreateEntityResponse)
def create_entity(request: CreateEntityRequest, session: DbSession) -> CreateEntityResponse:
    return run_audited_mutation(session, lambda: EntityService(session).create_entity(request))


@router.get("/entities/{entity_id}", response_model=EntityResponse)
def get_entity(entity_id: uuid.UUID, session: DbSession) -> EntityResponse:
    return EntityService(session).get(entity_id)
