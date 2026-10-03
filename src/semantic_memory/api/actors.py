"""HTTP endpoints for actor operations."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from semantic_memory.api.deps import require_admin_token
from semantic_memory.db import get_db_session
from semantic_memory.schemas.actors import ActorEnsureRequest, ActorResponse
from semantic_memory.services.actors import ActorService

router = APIRouter(prefix="/v1", tags=["actors"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post(
    "/actors/ensure",
    response_model=ActorResponse,
    dependencies=[Depends(require_admin_token)],
)
def ensure_actor(request: ActorEnsureRequest, session: DbSession) -> ActorResponse:
    try:
        result = ActorService(session).ensure(request)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


@router.get("/actors/{actor_id}", response_model=ActorResponse)
def get_actor(actor_id: uuid.UUID, session: DbSession) -> ActorResponse:
    return ActorService(session).get(actor_id)
