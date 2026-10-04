"""Read-only administrative inspection HTTP endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from semantic_memory.api.deps import require_admin_token
from semantic_memory.db import get_db_session
from semantic_memory.schemas.admin import (
    AdminSummaryResponse,
    BatchDetail,
    BatchListResponse,
    ConflictDetail,
    ConflictListResponse,
    FeedbackDetail,
    FeedbackListResponse,
    LlmCallDetail,
    LlmCallListResponse,
    OperationDetail,
    OperationListResponse,
    ProposalDetail,
    ProposalListResponse,
)
from semantic_memory.services.admin import AdminInspectionService

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)
DbSession = Annotated[Session, Depends(get_db_session)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/operations", response_model=OperationListResponse)
def list_operations(
    session: DbSession,
    status: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_key: str | None = None,
    operation_name: str | None = None,
    error_code: str | None = None,
    request_id: uuid.UUID | None = None,
    trace_id: uuid.UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OperationListResponse:
    return AdminInspectionService(session).list_operations(
        status=status,
        actor_id=actor_id,
        actor_key=actor_key,
        operation_name=operation_name,
        error_code=error_code,
        request_id=request_id,
        trace_id=trace_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/operations/{operation_id}", response_model=OperationDetail)
def get_operation(
    operation_id: uuid.UUID,
    session: DbSession,
    include_payloads: bool = False,
) -> OperationDetail:
    return AdminInspectionService(session).get_operation(
        operation_id, include_payloads=include_payloads
    )


@router.get("/llm-calls", response_model=LlmCallListResponse)
def list_llm_calls(
    session: DbSession,
    status: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_key: str | None = None,
    purpose: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    outcome: str | None = None,
    request_id: uuid.UUID | None = None,
    trace_id: uuid.UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> LlmCallListResponse:
    return AdminInspectionService(session).list_llm_calls(
        status=status,
        actor_id=actor_id,
        actor_key=actor_key,
        purpose=purpose,
        provider=provider,
        model=model,
        outcome=outcome,
        request_id=request_id,
        trace_id=trace_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/llm-calls/{call_id}", response_model=LlmCallDetail)
def get_llm_call(
    call_id: uuid.UUID,
    session: DbSession,
    include_payloads: bool = False,
) -> LlmCallDetail:
    return AdminInspectionService(session).get_llm_call(call_id, include_payloads=include_payloads)


@router.get("/feedback", response_model=FeedbackListResponse)
def list_feedback(
    session: DbSession,
    status: str | None = None,
    feedback_type: str | None = None,
    severity: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_key: str | None = None,
    entity_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    trace_id: uuid.UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> FeedbackListResponse:
    return AdminInspectionService(session).list_feedback(
        status=status,
        feedback_type=feedback_type,
        severity=severity,
        actor_id=actor_id,
        actor_key=actor_key,
        entity_id=entity_id,
        proposal_id=proposal_id,
        request_id=request_id,
        trace_id=trace_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/feedback/{feedback_id}", response_model=FeedbackDetail)
def get_feedback(
    feedback_id: uuid.UUID,
    session: DbSession,
    include_payloads: bool = False,
) -> FeedbackDetail:
    return AdminInspectionService(session).get_feedback(
        feedback_id, include_payloads=include_payloads
    )


@router.get("/proposals", response_model=ProposalListResponse)
def list_proposals(
    session: DbSession,
    status: str | None = None,
    proposal_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_key: str | None = None,
    request_id: uuid.UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ProposalListResponse:
    return AdminInspectionService(session).list_proposals(
        status=status,
        proposal_type=proposal_type,
        actor_id=actor_id,
        actor_key=actor_key,
        request_id=request_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/proposals/{proposal_id}", response_model=ProposalDetail)
def get_proposal(
    proposal_id: uuid.UUID,
    session: DbSession,
    include_payloads: bool = False,
) -> ProposalDetail:
    return AdminInspectionService(session).get_proposal(
        proposal_id, include_payloads=include_payloads
    )


@router.get("/conflicts", response_model=ConflictListResponse)
def list_conflicts(
    session: DbSession,
    status: str | None = None,
    entity_id: uuid.UUID | None = None,
    statement_id: uuid.UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ConflictListResponse:
    return AdminInspectionService(session).list_conflicts(
        status=status,
        entity_id=entity_id,
        statement_id=statement_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/conflicts/{conflict_id}", response_model=ConflictDetail)
def get_conflict(conflict_id: uuid.UUID, session: DbSession) -> ConflictDetail:
    return AdminInspectionService(session).get_conflict(conflict_id)


@router.get("/batches", response_model=BatchListResponse)
def list_batches(
    session: DbSession,
    status: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_key: str | None = None,
    request_id: uuid.UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> BatchListResponse:
    return AdminInspectionService(session).list_batches(
        status=status,
        actor_id=actor_id,
        actor_key=actor_key,
        request_id=request_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/batches/{batch_id}", response_model=BatchDetail)
def get_batch(
    batch_id: uuid.UUID,
    session: DbSession,
    include_payloads: bool = False,
) -> BatchDetail:
    return AdminInspectionService(session).get_batch(batch_id, include_payloads=include_payloads)


@router.get("/summary", response_model=AdminSummaryResponse)
def admin_summary(
    session: DbSession,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> AdminSummaryResponse:
    return AdminInspectionService(session).summary(
        created_after=created_after,
        created_before=created_before,
    )
