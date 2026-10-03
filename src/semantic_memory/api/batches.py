"""HTTP endpoints for batch ingestion."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.schemas.batches import AssertBatchRequest, AssertBatchResponse
from semantic_memory.services.batches import BatchService

router = APIRouter(prefix="/v1", tags=["batches"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post("/batches/assert", response_model=AssertBatchResponse)
def assert_batch(request: AssertBatchRequest, session: DbSession) -> AssertBatchResponse:
    return run_audited_mutation(session, lambda: BatchService(session).assert_batch(request))
