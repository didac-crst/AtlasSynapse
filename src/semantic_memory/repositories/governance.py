"""Ontology proposal, gate result, and change persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
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

    def list_proposals(
        self,
        *,
        status: str | None = None,
        proposal_type: str | None = None,
        proposed_by_actor_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OntologyProposal]:
        stmt = self._proposal_filtered_select(
            status=status,
            proposal_type=proposal_type,
            proposed_by_actor_id=proposed_by_actor_id,
            request_id=request_id,
            created_after=created_after,
            created_before=created_before,
        ).order_by(OntologyProposal.created_at.desc(), OntologyProposal.id.desc())
        return list(self._session.scalars(stmt.limit(limit).offset(offset)).all())

    def count_proposals(
        self,
        *,
        status: str | None = None,
        proposal_type: str | None = None,
        proposed_by_actor_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        filtered = self._proposal_filtered_select(
            status=status,
            proposal_type=proposal_type,
            proposed_by_actor_id=proposed_by_actor_id,
            request_id=request_id,
            created_after=created_after,
            created_before=created_before,
        ).subquery()
        return int(self._session.scalar(select(func.count()).select_from(filtered)) or 0)

    def aggregate_proposal_summary(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        by_status_rows = self._session.execute(
            self._proposal_time_filters(
                select(OntologyProposal.status, func.count()),
                created_after=created_after,
                created_before=created_before,
            ).group_by(OntologyProposal.status)
        )
        by_type_rows = self._session.execute(
            self._proposal_time_filters(
                select(OntologyProposal.proposal_type, func.count()),
                created_after=created_after,
                created_before=created_before,
            ).group_by(OntologyProposal.proposal_type)
        )
        by_status = {str(k): int(v) for k, v in by_status_rows}
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_type": {str(k): int(v) for k, v in by_type_rows},
        }

    def _proposal_filtered_select(
        self,
        *,
        status: str | None,
        proposal_type: str | None,
        proposed_by_actor_id: uuid.UUID | None,
        request_id: uuid.UUID | None,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        stmt = select(OntologyProposal)
        if status is not None:
            stmt = stmt.where(OntologyProposal.status == status)
        if proposal_type is not None:
            stmt = stmt.where(OntologyProposal.proposal_type == proposal_type)
        if proposed_by_actor_id is not None:
            stmt = stmt.where(OntologyProposal.proposed_by_actor_id == proposed_by_actor_id)
        if request_id is not None:
            stmt = stmt.where(OntologyProposal.request_id == request_id)
        return self._proposal_time_filters(
            stmt, created_after=created_after, created_before=created_before
        )

    @staticmethod
    def _proposal_time_filters(
        stmt: Any,
        *,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        if created_after is not None:
            stmt = stmt.where(OntologyProposal.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(OntologyProposal.created_at <= created_before)
        return stmt

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
