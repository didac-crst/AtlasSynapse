"""HTTP endpoints for governed ontology proposals."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from semantic_memory.api.transactions import run_audited_mutation
from semantic_memory.db import get_db_session
from semantic_memory.schemas.proposals import (
    ApplyProposalRequest,
    ApplyProposalResponse,
    ProposalResponse,
    ProposeAliasRequest,
    ProposeClassParentRequest,
    ProposeClassRequest,
    ProposeConstraintRequest,
    ProposePredicateRequest,
    ProposeResponse,
)
from semantic_memory.services.proposals import ProposalService

router = APIRouter(prefix="/v1/ontology/proposals", tags=["ontology-proposals"])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.get("/{proposal_id}", response_model=ProposalResponse)
def get_proposal(proposal_id: uuid.UUID, session: DbSession) -> ProposalResponse:
    return ProposalService(session).get_proposal(proposal_id)


@router.post("/classes", response_model=ProposeResponse)
def propose_class(request: ProposeClassRequest, session: DbSession) -> ProposeResponse:
    return run_audited_mutation(session, lambda: ProposalService(session).propose_class(request))


@router.post("/predicates", response_model=ProposeResponse)
def propose_predicate(request: ProposePredicateRequest, session: DbSession) -> ProposeResponse:
    return run_audited_mutation(
        session, lambda: ProposalService(session).propose_predicate(request)
    )


@router.post("/constraints", response_model=ProposeResponse)
def propose_constraint(request: ProposeConstraintRequest, session: DbSession) -> ProposeResponse:
    return run_audited_mutation(
        session, lambda: ProposalService(session).propose_constraint(request)
    )


@router.post("/aliases", response_model=ProposeResponse)
def propose_alias(request: ProposeAliasRequest, session: DbSession) -> ProposeResponse:
    return run_audited_mutation(session, lambda: ProposalService(session).propose_alias(request))


@router.post("/class-parents", response_model=ProposeResponse)
def propose_class_parent(request: ProposeClassParentRequest, session: DbSession) -> ProposeResponse:
    return run_audited_mutation(
        session, lambda: ProposalService(session).propose_class_parent(request)
    )


@router.post("/apply", response_model=ApplyProposalResponse)
def apply_proposal(request: ApplyProposalRequest, session: DbSession) -> ApplyProposalResponse:
    return run_audited_mutation(session, lambda: ProposalService(session).apply_proposal(request))
