"""Ontology proposal, gate result, and change persistence."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    GateDecision,
    OntologyChange,
    OntologyChangeObjectType,
    OntologyGateResult,
    OntologyProposal,
    ProposalStatus,
)


class GovernanceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_proposal(self, proposal_id: uuid.UUID) -> OntologyProposal | None:
        return self._session.get(OntologyProposal, proposal_id)

    def create_proposal(
        self,
        *,
        proposed_by_actor_id: uuid.UUID,
        proposal_type: str,
        summary: str,
        payload: dict[str, Any],
        request_id: uuid.UUID,
        base_revision_number: int | None = None,
        status: ProposalStatus = ProposalStatus.SUBMITTED,
    ) -> OntologyProposal:
        row = OntologyProposal(
            id=uuid.uuid4(),
            proposed_by_actor_id=proposed_by_actor_id,
            status=status.value,
            proposal_type=proposal_type,
            summary=summary,
            payload=payload,
            base_revision_number=base_revision_number,
            request_id=request_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def set_proposal_status(
        self,
        proposal: OntologyProposal,
        *,
        status: ProposalStatus,
        decision_reason: str | None = None,
    ) -> OntologyProposal:
        proposal.status = status.value
        if decision_reason is not None:
            proposal.decision_reason = decision_reason
        self._session.flush()
        return proposal

    def upsert_gate_result(
        self,
        *,
        proposal_id: uuid.UUID,
        gate_name: str,
        decision: GateDecision,
        details: dict[str, Any] | None = None,
    ) -> OntologyGateResult:
        existing = self._session.scalar(
            select(OntologyGateResult).where(
                OntologyGateResult.proposal_id == proposal_id,
                OntologyGateResult.gate_name == gate_name,
            )
        )
        if existing is not None:
            existing.decision = decision.value
            existing.details = details or {}
            self._session.flush()
            return existing
        row = OntologyGateResult(
            id=uuid.uuid4(),
            proposal_id=proposal_id,
            gate_name=gate_name,
            decision=decision.value,
            details=details or {},
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_gate_results(self, proposal_id: uuid.UUID) -> list[OntologyGateResult]:
        return list(
            self._session.scalars(
                select(OntologyGateResult)
                .where(OntologyGateResult.proposal_id == proposal_id)
                .order_by(OntologyGateResult.created_at.asc(), OntologyGateResult.gate_name.asc())
            ).all()
        )

    def create_change(
        self,
        *,
        proposal_id: uuid.UUID,
        object_type: OntologyChangeObjectType,
        object_id: uuid.UUID,
        change_summary: str,
        applied_by_actor_id: uuid.UUID,
        previous_revision_id: uuid.UUID | None = None,
        new_revision_id: uuid.UUID | None = None,
    ) -> OntologyChange:
        row = OntologyChange(
            id=uuid.uuid4(),
            proposal_id=proposal_id,
            object_type=object_type.value,
            object_id=object_id,
            previous_revision_id=previous_revision_id,
            new_revision_id=new_revision_id,
            change_summary=change_summary,
            applied_by_actor_id=applied_by_actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_changes(self, proposal_id: uuid.UUID) -> list[OntologyChange]:
        return list(
            self._session.scalars(
                select(OntologyChange)
                .where(OntologyChange.proposal_id == proposal_id)
                .order_by(OntologyChange.created_at.asc())
            ).all()
        )
