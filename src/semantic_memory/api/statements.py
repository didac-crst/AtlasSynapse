"""HTTP endpoints for statement operations."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.schemas.memory_quality import (
    RepairSupersessionIntegrityRequest,
    RepairSupersessionIntegrityResponse,
)
from semantic_memory.schemas.provenance import ExplainStatementResponse
from semantic_memory.schemas.statements import (
    AssertStatementRequest,
    AssertStatementResponse,
    CorrectStatementRequest,
    RetractStatementRequest,
    RetractStatementResponse,
    StatementResponse,
    SupersedeStatementRequest,
    SupersedeStatementResponse,
    TimelineResponse,
)
from semantic_memory.schemas.write_clarifications import (
    AnswerIdentityClarificationRequest,
    AnswerIdentityClarificationResponse,
)
from semantic_memory.services.memory_quality import SupersessionIntegrityRepairService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.statements import StatementService
from semantic_memory.services.write_clarifications import WriteClarificationService

router = APIRouter(prefix="/v1", tags=["statements"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.post("/statements", response_model=AssertStatementResponse)
def assert_statement(
    request: AssertStatementRequest, session: DbSession
) -> AssertStatementResponse:
    return run_audited_mutation(
        session, lambda: StatementService(session).assert_statement(request)
    )


@router.post(
    "/statements/answer-identity-clarification",
    response_model=AnswerIdentityClarificationResponse,
)
def answer_identity_clarification(
    request: AnswerIdentityClarificationRequest, session: DbSession
) -> AnswerIdentityClarificationResponse:
    return run_audited_mutation(session, lambda: WriteClarificationService(session).answer(request))


@router.get("/statements/{statement_id}", response_model=StatementResponse)
def get_statement(statement_id: uuid.UUID, session: DbSession) -> StatementResponse:
    return StatementService(session).get(statement_id)


@router.get("/statements/{statement_id}/explain", response_model=ExplainStatementResponse)
def explain_statement(statement_id: uuid.UUID, session: DbSession) -> ExplainStatementResponse:
    return ProvenanceService(session).explain_statement(statement_id)


@router.post("/statements/supersede", response_model=SupersedeStatementResponse)
def supersede_statement(
    request: SupersedeStatementRequest, session: DbSession
) -> SupersedeStatementResponse:
    return run_audited_mutation(
        session, lambda: StatementService(session).supersede_statement(request)
    )


@router.post("/statements/correct", response_model=SupersedeStatementResponse)
def correct_statement(
    request: CorrectStatementRequest, session: DbSession
) -> SupersedeStatementResponse:
    return run_audited_mutation(
        session, lambda: StatementService(session).correct_statement(request)
    )


@router.post("/statements/retract", response_model=RetractStatementResponse)
def retract_statement(
    request: RetractStatementRequest, session: DbSession
) -> RetractStatementResponse:
    return run_audited_mutation(
        session, lambda: StatementService(session).retract_statement(request)
    )


@router.post(
    "/memory-quality/repair-supersession",
    response_model=RepairSupersessionIntegrityResponse,
)
def repair_supersession_integrity(
    request: RepairSupersessionIntegrityRequest, session: DbSession
) -> RepairSupersessionIntegrityResponse:
    return run_audited_mutation(
        session,
        lambda: SupersessionIntegrityRepairService(session).repair(request),
    )


@router.get("/entities/{entity_id}/timeline", response_model=TimelineResponse)
def get_timeline(entity_id: uuid.UUID, session: DbSession) -> TimelineResponse:
    return StatementService(session).get_timeline(entity_id)
