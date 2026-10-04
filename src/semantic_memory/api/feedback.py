"""HTTP endpoints for agent feedback reporting and admin resolution."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.models.enums import FeedbackStatus, FeedbackType
from semantic_memory.schemas.feedback import (
    FeedbackResponse,
    ListFeedbackResponse,
    ReportFeedbackRequest,
    ReportFeedbackResponse,
    ResolveFeedbackRequest,
    ResolveFeedbackResponse,
)
from semantic_memory.services.feedback import FeedbackService

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post("/", response_model=ReportFeedbackResponse)
def report_feedback(request: ReportFeedbackRequest, session: DbSession) -> ReportFeedbackResponse:
    return run_audited_mutation(session, lambda: FeedbackService(session).report_feedback(request))


@router.get("/", response_model=ListFeedbackResponse)
def list_feedback(
    session: DbSession,
    actor_key: Annotated[str, Query(min_length=1)],
    status: FeedbackStatus | None = None,
    feedback_type: FeedbackType | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListFeedbackResponse:
    return FeedbackService(session).list_feedback(
        actor_key=actor_key,
        status=status,
        feedback_type=None if feedback_type is None else feedback_type.value,
        limit=limit,
    )


@router.get("/{feedback_id}", response_model=FeedbackResponse)
def get_feedback(
    feedback_id: uuid.UUID,
    session: DbSession,
    actor_key: Annotated[str, Query(min_length=1)],
) -> FeedbackResponse:
    return FeedbackService(session).get_feedback(feedback_id, actor_key=actor_key)


@router.post("/resolve", response_model=ResolveFeedbackResponse)
def resolve_feedback(
    request: ResolveFeedbackRequest, session: DbSession
) -> ResolveFeedbackResponse:
    return run_audited_mutation(session, lambda: FeedbackService(session).resolve_feedback(request))
