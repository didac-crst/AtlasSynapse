"""Read-only administrative inspection over operational and governance state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    UnknownBatchError,
    UnknownConflictError,
    UnknownFeedbackError,
    UnknownLlmCallError,
    UnknownOperationError,
    UnknownProposalError,
    ValidationFailedError,
)
from semantic_memory.models.feedback import AgentFeedback
from semantic_memory.models.governance import OntologyProposal
from semantic_memory.models.llm_calls import LlmCallLog
from semantic_memory.models.operations import IngestionBatch, OperationLog
from semantic_memory.models.reasoning import Conflict
from semantic_memory.repositories.actors import ActorRepository
from semantic_memory.repositories.batches import BatchRepository
from semantic_memory.repositories.conflicts import ConflictRepository
from semantic_memory.repositories.feedback import FeedbackRepository
from semantic_memory.repositories.governance import GovernanceRepository
from semantic_memory.repositories.llm_calls import LlmCallLogRepository
from semantic_memory.repositories.operations import OperationLogRepository
from semantic_memory.schemas.admin import (
    AdminSummaryResponse,
    BatchDetail,
    BatchesSummaryBlock,
    BatchListResponse,
    BatchSummary,
    ConflictDetail,
    ConflictListResponse,
    ConflictsSummaryBlock,
    ConflictSummary,
    FeedbackDetail,
    FeedbackListResponse,
    FeedbackSummary,
    FeedbackSummaryBlock,
    LlmCallDetail,
    LlmCallListResponse,
    LlmCallsSummaryBlock,
    LlmCallSummary,
    OperationDetail,
    OperationListResponse,
    OperationsSummaryBlock,
    OperationSummary,
    ProposalDetail,
    ProposalListResponse,
    ProposalsSummaryBlock,
    ProposalSummary,
)


class AdminInspectionService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorRepository(session)
        self._operations = OperationLogRepository(session)
        self._llm_calls = LlmCallLogRepository(session)
        self._feedback = FeedbackRepository(session)
        self._governance = GovernanceRepository(session)
        self._conflicts = ConflictRepository(session)
        self._batches = BatchRepository(session)

    def list_operations(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        actor_key: str | None = None,
        operation_name: str | None = None,
        error_code: str | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> OperationListResponse:
        self._validate_range(created_after, created_before)
        resolved_actor = self._resolve_actor_id(actor_id=actor_id, actor_key=actor_key)
        filters = dict(
            status=status,
            actor_id=resolved_actor,
            operation_name=operation_name,
            error_code=error_code,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        )
        items = self._operations.list(**cast(Any, filters), limit=limit, offset=offset)
        total = self._operations.count(**cast(Any, filters))
        return OperationListResponse(
            items=[self._operation_summary(row) for row in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_operation(
        self, operation_id: uuid.UUID, *, include_payloads: bool = False
    ) -> OperationDetail:
        row = self._operations.get(operation_id)
        if row is None:
            raise UnknownOperationError(
                f"Operation {operation_id} was not found",
                details={"operation_id": str(operation_id)},
            )
        summary = self._operation_summary(row)
        return OperationDetail(
            **summary.model_dump(),
            request_payload=row.request_payload if include_payloads else None,
            response_payload=row.response_payload if include_payloads else None,
        )

    def list_llm_calls(
        self,
        *,
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
        limit: int = 50,
        offset: int = 0,
    ) -> LlmCallListResponse:
        self._validate_range(created_after, created_before)
        resolved_actor = self._resolve_actor_id(actor_id=actor_id, actor_key=actor_key)
        filters = dict(
            status=status,
            actor_id=resolved_actor,
            purpose=purpose,
            provider=provider,
            model=model,
            outcome=outcome,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        )
        items = self._llm_calls.list(**cast(Any, filters), limit=limit, offset=offset)
        total = self._llm_calls.count(**cast(Any, filters))
        return LlmCallListResponse(
            items=[self._llm_summary(row) for row in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_llm_call(self, call_id: uuid.UUID, *, include_payloads: bool = False) -> LlmCallDetail:
        row = self._llm_calls.get(call_id)
        if row is None:
            raise UnknownLlmCallError(
                f"LLM call {call_id} was not found",
                details={"llm_call_id": str(call_id)},
            )
        summary = self._llm_summary(row)
        return LlmCallDetail(
            **summary.model_dump(),
            model_version=row.model_version,
            reason=row.reason,
            cached_input_tokens=row.cached_input_tokens,
            pricing_version=row.pricing_version if include_payloads else None,
            pricing_snapshot=row.pricing_snapshot if include_payloads else None,
            error_message=row.error_message,
            metadata=dict(row.metadata_json or {}) if include_payloads else None,
        )

    def list_feedback(
        self,
        *,
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
        limit: int = 50,
        offset: int = 0,
    ) -> FeedbackListResponse:
        self._validate_range(created_after, created_before)
        resolved_actor = self._resolve_actor_id(actor_id=actor_id, actor_key=actor_key)
        filters = dict(
            status=status,
            feedback_type=feedback_type,
            severity=severity,
            actor_id=resolved_actor,
            entity_id=entity_id,
            proposal_id=proposal_id,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        )
        items = self._feedback.list(
            **cast(Any, filters), limit=limit, offset=offset, order_by_created=True
        )
        total = self._feedback.count(**cast(Any, filters))
        return FeedbackListResponse(
            items=[self._feedback_summary(row) for row in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_feedback(
        self, feedback_id: uuid.UUID, *, include_payloads: bool = False
    ) -> FeedbackDetail:
        row = self._feedback.get(feedback_id)
        if row is None:
            raise UnknownFeedbackError(
                f"Feedback {feedback_id} was not found",
                details={"feedback_id": str(feedback_id)},
            )
        summary = self._feedback_summary(row)
        return FeedbackDetail(
            **summary.model_dump(),
            description=row.description,
            resolution=row.resolution,
            resolved_by_actor_id=row.resolved_by_actor_id,
            resolved_at=row.resolved_at,
            context=dict(row.context or {}) if include_payloads else None,
        )

    def list_proposals(
        self,
        *,
        status: str | None = None,
        proposal_type: str | None = None,
        actor_id: uuid.UUID | None = None,
        actor_key: str | None = None,
        request_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ProposalListResponse:
        self._validate_range(created_after, created_before)
        resolved_actor = self._resolve_actor_id(actor_id=actor_id, actor_key=actor_key)
        filters = dict(
            status=status,
            proposal_type=proposal_type,
            proposed_by_actor_id=resolved_actor,
            request_id=request_id,
            created_after=created_after,
            created_before=created_before,
        )
        items = self._governance.list_proposals(**cast(Any, filters), limit=limit, offset=offset)
        total = self._governance.count_proposals(**cast(Any, filters))
        return ProposalListResponse(
            items=[self._proposal_summary(row) for row in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_proposal(
        self, proposal_id: uuid.UUID, *, include_payloads: bool = False
    ) -> ProposalDetail:
        row = self._governance.get_proposal(proposal_id)
        if row is None:
            raise UnknownProposalError(
                f"Proposal {proposal_id} was not found",
                details={"proposal_id": str(proposal_id)},
            )
        summary = self._proposal_summary(row)
        return ProposalDetail(
            **summary.model_dump(),
            payload=dict(row.payload or {}) if include_payloads else None,
        )

    def list_conflicts(
        self,
        *,
        status: str | None = None,
        entity_id: uuid.UUID | None = None,
        statement_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ConflictListResponse:
        self._validate_range(created_after, created_before)
        filters = dict(
            status=status,
            entity_id=entity_id,
            statement_id=statement_id,
            created_after=created_after,
            created_before=created_before,
        )
        items = self._conflicts.list_conflicts(
            **cast(Any, filters), limit=limit, offset=offset, newest_first=True
        )
        total = self._conflicts.count_conflicts(**cast(Any, filters))
        return ConflictListResponse(
            items=[self._conflict_summary(row) for row in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_conflict(self, conflict_id: uuid.UUID) -> ConflictDetail:
        row = self._conflicts.get(conflict_id)
        if row is None:
            raise UnknownConflictError(
                f"Conflict {conflict_id} was not found",
                details={"conflict_id": str(conflict_id)},
            )
        summary = self._conflict_summary(row)
        return ConflictDetail(**summary.model_dump(), details=dict(row.details or {}))

    def list_batches(
        self,
        *,
        status: str | None = None,
        actor_id: uuid.UUID | None = None,
        actor_key: str | None = None,
        request_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> BatchListResponse:
        self._validate_range(created_after, created_before)
        resolved_actor = self._resolve_actor_id(actor_id=actor_id, actor_key=actor_key)
        filters = dict(
            status=status,
            actor_id=resolved_actor,
            request_id=request_id,
            created_after=created_after,
            created_before=created_before,
        )
        items = self._batches.list(**cast(Any, filters), limit=limit, offset=offset)
        total = self._batches.count(**cast(Any, filters))
        return BatchListResponse(
            items=[self._batch_summary(row) for row in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_batch(self, batch_id: uuid.UUID, *, include_payloads: bool = False) -> BatchDetail:
        row = self._batches.get(batch_id)
        if row is None:
            raise UnknownBatchError(
                f"Batch {batch_id} was not found",
                details={"batch_id": str(batch_id)},
            )
        summary = self._batch_summary(row)
        return BatchDetail(
            **summary.model_dump(),
            metadata=dict(row.metadata_json or {}) if include_payloads else {},
        )

    def summary(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> AdminSummaryResponse:
        self._validate_range(created_after, created_before)
        ops = self._operations.aggregate_summary(
            created_after=created_after, created_before=created_before
        )
        llm = self._llm_calls.aggregate_summary(
            created_after=created_after, created_before=created_before
        )
        feedback = self._feedback.aggregate_summary(
            created_after=created_after, created_before=created_before
        )
        proposals = self._governance.aggregate_proposal_summary(
            created_after=created_after, created_before=created_before
        )
        conflicts = self._conflicts.aggregate_summary(
            created_after=created_after, created_before=created_before
        )
        batches = self._batches.aggregate_summary(
            created_after=created_after, created_before=created_before
        )
        return AdminSummaryResponse(
            operations=OperationsSummaryBlock(**ops),
            llm_calls=LlmCallsSummaryBlock(**llm),
            feedback=FeedbackSummaryBlock(**feedback),
            proposals=ProposalsSummaryBlock(**proposals),
            conflicts=ConflictsSummaryBlock(**conflicts),
            batches=BatchesSummaryBlock(**batches),
        )

    def _resolve_actor_id(
        self, *, actor_id: uuid.UUID | None, actor_key: str | None
    ) -> uuid.UUID | None:
        if actor_id is not None and actor_key is not None:
            actor = self._actors.find_by_key(actor_key)
            if actor is None or actor.id != actor_id:
                raise ValidationFailedError(
                    "actor_id and actor_key do not refer to the same actor",
                    details={
                        "actor_id": str(actor_id),
                        "actor_key": actor_key,
                    },
                )
            return actor_id
        if actor_id is not None:
            return actor_id
        if actor_key is not None:
            actor = self._actors.find_by_key(actor_key)
            if actor is None:
                raise ValidationFailedError(
                    f"Unknown actor_key '{actor_key}'",
                    details={"actor_key": actor_key},
                )
            return actor.id
        return None

    @staticmethod
    def _validate_range(created_after: datetime | None, created_before: datetime | None) -> None:
        if (
            created_after is not None
            and created_before is not None
            and created_after > created_before
        ):
            raise ValidationFailedError(
                "created_after must be less than or equal to created_before",
                details={
                    "created_after": created_after.isoformat(),
                    "created_before": created_before.isoformat(),
                },
            )

    @staticmethod
    def _operation_summary(row: OperationLog) -> OperationSummary:
        return OperationSummary(
            id=row.id,
            actor_id=row.actor_id,
            operation_name=row.operation_name,
            status=row.status,
            error_code=row.error_code,
            error_message=row.error_message,
            request_id=row.request_id,
            trace_id=row.trace_id,
            idempotency_key=row.idempotency_key,
            started_at=row.started_at,
            finished_at=row.finished_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _llm_summary(row: LlmCallLog) -> LlmCallSummary:
        return LlmCallSummary(
            id=row.id,
            actor_id=row.actor_id,
            operation_log_id=row.operation_log_id,
            request_id=row.request_id,
            trace_id=row.trace_id,
            provider=row.provider,
            model=row.model,
            purpose=row.purpose,
            status=row.status,
            outcome=row.outcome,
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            total_tokens=row.total_tokens,
            cost_amount=row.cost_amount,
            cost_currency=row.cost_currency,
            cost_status=row.cost_status,
            error_code=row.error_code,
            created_at=row.created_at,
        )

    @staticmethod
    def _feedback_summary(row: AgentFeedback) -> FeedbackSummary:
        return FeedbackSummary(
            id=row.id,
            actor_id=row.actor_id,
            feedback_type=row.feedback_type,
            severity=row.severity,
            title=row.title,
            status=row.status,
            request_id=row.request_id,
            trace_id=row.trace_id,
            operation_log_id=row.operation_log_id,
            proposal_id=row.proposal_id,
            entity_id=row.entity_id,
            statement_id=row.statement_id,
            source_id=row.source_id,
            fingerprint=row.fingerprint,
            occurrence_count=row.occurrence_count,
            last_seen_at=row.last_seen_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _proposal_summary(row: OntologyProposal) -> ProposalSummary:
        return ProposalSummary(
            id=row.id,
            proposed_by_actor_id=row.proposed_by_actor_id,
            status=row.status,
            proposal_type=row.proposal_type,
            summary=row.summary,
            base_revision_number=row.base_revision_number,
            request_id=row.request_id,
            decision_reason=row.decision_reason,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _conflict_summary(row: Conflict) -> ConflictSummary:
        return ConflictSummary(
            id=row.id,
            statement_a_id=row.statement_a_id,
            statement_b_id=row.statement_b_id,
            status=row.status,
            conflict_type=row.conflict_type,
            resolved_by_actor_id=row.resolved_by_actor_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _batch_summary(row: IngestionBatch) -> BatchSummary:
        return BatchSummary(
            id=row.id,
            actor_id=row.actor_id,
            status=row.status,
            item_count=row.item_count,
            request_id=row.request_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
