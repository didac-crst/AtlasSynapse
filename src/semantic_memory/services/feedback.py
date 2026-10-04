"""Agent feedback reporting, listing, and resolution."""

from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.exceptions import (
    InvalidStateTransitionError,
    UnknownEntityError,
    UnknownFeedbackError,
    UnknownProposalError,
    UnknownSourceError,
    UnknownStatementError,
    ValidationFailedError,
)
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import (
    FeedbackOutcome,
    FeedbackSeverity,
    FeedbackStatus,
    FeedbackType,
)
from semantic_memory.models.feedback import AgentFeedback
from semantic_memory.models.operations import OperationLog
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.feedback import FeedbackRepository
from semantic_memory.repositories.governance import GovernanceRepository
from semantic_memory.repositories.provenance import ProvenanceRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.feedback import (
    FeedbackResponse,
    ListFeedbackResponse,
    ReportFeedbackRequest,
    ReportFeedbackResponse,
    ResolveFeedbackRequest,
    ResolveFeedbackResponse,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.mutations import MutationRunner
from semantic_memory.services.redaction import prepare_audit_payload

_WS_RE = re.compile(r"\s+")


def _normalize_text(value: str) -> str:
    return _WS_RE.sub(" ", value.strip().casefold())


def compute_feedback_fingerprint(
    *,
    feedback_type: str,
    title: str,
    description: str,
    entity_id: uuid.UUID | None,
    statement_id: uuid.UUID | None,
    source_id: uuid.UUID | None,
    proposal_id: uuid.UUID | None,
    operation_log_id: uuid.UUID | None,
) -> str:
    material = "|".join(
        [
            feedback_type,
            _normalize_text(title),
            _normalize_text(description),
            str(entity_id or ""),
            str(statement_id or ""),
            str(source_id or ""),
            str(proposal_id or ""),
            str(operation_log_id or ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class FeedbackService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._actors = ActorService(session)
        self._feedback = FeedbackRepository(session)
        self._entities = EntityRepository(session)
        self._statements = StatementRepository(session)
        self._provenance = ProvenanceRepository(session)
        self._governance = GovernanceRepository(session)
        self._mutations = MutationRunner(session, self._settings)

    def report_feedback(self, request: ReportFeedbackRequest) -> ReportFeedbackResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.FEEDBACK_CREATE)
        return self._mutations.run(
            actor=actor,
            operation_name="report_feedback",
            request=request,
            response_model=ReportFeedbackResponse,
            constraint_name="feedback_report",
            execute=lambda: self._report_body(request=request, actor_id=actor.id),
        )

    def get_feedback(self, feedback_id: uuid.UUID, *, actor_key: str) -> FeedbackResponse:
        actor = self._actors.require_active_actor(actor_key)
        self._actors.require_capability(actor, Capability.FEEDBACK_READ)
        row = self._feedback.get(feedback_id)
        if row is None:
            raise UnknownFeedbackError(
                f"Feedback {feedback_id} was not found",
                details={"feedback_id": str(feedback_id)},
            )
        return self._to_response(row)

    def list_feedback(
        self,
        *,
        actor_key: str,
        status: FeedbackStatus | None = None,
        feedback_type: str | None = None,
        limit: int = 50,
    ) -> ListFeedbackResponse:
        actor = self._actors.require_active_actor(actor_key)
        self._actors.require_capability(actor, Capability.FEEDBACK_READ)
        rows = self._feedback.list(
            status=None if status is None else status.value,
            feedback_type=feedback_type,
            limit=limit,
            offset=0,
            order_by_created=False,
        )
        return ListFeedbackResponse(items=[self._to_response(row) for row in rows])

    def resolve_feedback(self, request: ResolveFeedbackRequest) -> ResolveFeedbackResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.FEEDBACK_MANAGE)
        if request.status == FeedbackStatus.OPEN:
            raise ValidationFailedError(
                "Resolve status must be acknowledged, resolved, or dismissed",
                details={"status": request.status.value},
                request_id=str(request.request_id),
            )
        return self._mutations.run(
            actor=actor,
            operation_name="resolve_feedback",
            request=request,
            response_model=ResolveFeedbackResponse,
            constraint_name="feedback_resolve",
            execute=lambda: self._resolve_body(request=request, actor_id=actor.id),
        )

    def _report_body(
        self, *, request: ReportFeedbackRequest, actor_id: uuid.UUID
    ) -> ReportFeedbackResponse:
        self._validate_links(request)
        context = (
            prepare_audit_payload(
                request.context,
                mode=self._settings.raw_payload_retention,
            )
            or {}
        )
        # Title/description are the observation; keep them, but never store secrets in context.
        fingerprint = compute_feedback_fingerprint(
            feedback_type=request.feedback_type.value,
            title=request.title,
            description=request.description,
            entity_id=request.entity_id,
            statement_id=request.statement_id,
            source_id=request.source_id,
            proposal_id=request.proposal_id,
            operation_log_id=request.operation_log_id,
        )
        existing = self._feedback.find_open_by_fingerprint(fingerprint)
        if existing is not None:
            touched = self._feedback.touch_occurrence(existing)
            return ReportFeedbackResponse(
                outcome=FeedbackOutcome.DEDUPED,
                feedback=self._to_response(touched),
                request_id=request.request_id,
            )

        # Prefer correlating the current mutation's request/trace for this actor.
        operation = self._session.scalar(
            select(OperationLog)
            .where(
                OperationLog.actor_id == actor_id,
                OperationLog.request_id == request.request_id,
            )
            .order_by(OperationLog.started_at.desc())
            .limit(1)
        )
        correlated_trace_id = (
            request.trace_id
            if request.trace_id is not None
            else (None if operation is None else operation.trace_id)
        )
        correlated_operation_log_id = (
            request.operation_log_id
            if request.operation_log_id is not None
            else (None if operation is None else operation.id)
        )
        try:
            # Nested savepoint so a concurrent open-fingerprint conflict
            # rolls back only the insert, not the surrounding mutation audit.
            with self._session.begin_nested():
                row = self._feedback.create(
                    actor_id=actor_id,
                    feedback_type=request.feedback_type.value,
                    severity=request.severity.value,
                    title=request.title.strip(),
                    description=request.description.strip(),
                    request_id=request.request_id,
                    trace_id=correlated_trace_id,
                    operation_log_id=correlated_operation_log_id,
                    proposal_id=request.proposal_id,
                    entity_id=request.entity_id,
                    statement_id=request.statement_id,
                    source_id=request.source_id,
                    context=context,
                    fingerprint=fingerprint,
                )
        except IntegrityError:
            raced = self._feedback.find_open_by_fingerprint(fingerprint)
            if raced is None:
                raise
            touched = self._feedback.touch_occurrence(raced)
            return ReportFeedbackResponse(
                outcome=FeedbackOutcome.DEDUPED,
                feedback=self._to_response(touched),
                request_id=request.request_id,
            )
        return ReportFeedbackResponse(
            outcome=FeedbackOutcome.CREATE,
            feedback=self._to_response(row),
            request_id=request.request_id,
        )

    def _resolve_body(
        self, *, request: ResolveFeedbackRequest, actor_id: uuid.UUID
    ) -> ResolveFeedbackResponse:
        row = self._feedback.get(request.feedback_id)
        if row is None:
            raise UnknownFeedbackError(
                f"Feedback {request.feedback_id} was not found",
                details={"feedback_id": str(request.feedback_id)},
                request_id=str(request.request_id),
            )
        if row.status != FeedbackStatus.OPEN.value:
            raise InvalidStateTransitionError(
                "Only open feedback can be resolved",
                details={"feedback_id": str(row.id), "status": row.status},
                request_id=str(request.request_id),
            )
        resolved = self._feedback.resolve(
            row,
            status=request.status.value,
            resolution=None if request.resolution is None else request.resolution.strip(),
            resolved_by_actor_id=actor_id,
        )
        return ResolveFeedbackResponse(
            feedback=self._to_response(resolved),
            request_id=request.request_id,
        )

    def _validate_links(self, request: ReportFeedbackRequest) -> None:
        if request.entity_id is not None and self._entities.get(request.entity_id) is None:
            raise UnknownEntityError(
                f"Entity {request.entity_id} was not found",
                details={"entity_id": str(request.entity_id)},
                request_id=str(request.request_id),
            )
        if request.statement_id is not None and self._statements.get(request.statement_id) is None:
            raise UnknownStatementError(
                f"Statement {request.statement_id} was not found",
                details={"statement_id": str(request.statement_id)},
                request_id=str(request.request_id),
            )
        if request.source_id is not None and self._provenance.get_source(request.source_id) is None:
            raise UnknownSourceError(
                f"Source {request.source_id} was not found",
                details={"source_id": str(request.source_id)},
                request_id=str(request.request_id),
            )
        if request.proposal_id is not None:
            proposal = self._governance.get_proposal(request.proposal_id)
            if proposal is None:
                raise UnknownProposalError(
                    f"Proposal {request.proposal_id} was not found",
                    details={"proposal_id": str(request.proposal_id)},
                    request_id=str(request.request_id),
                )
        if request.operation_log_id is not None:
            op = self._session.get(OperationLog, request.operation_log_id)
            if op is None:
                raise ValidationFailedError(
                    f"Operation log {request.operation_log_id} was not found",
                    details={"operation_log_id": str(request.operation_log_id)},
                    request_id=str(request.request_id),
                )

    def _to_response(self, row: AgentFeedback) -> FeedbackResponse:
        return FeedbackResponse(
            id=row.id,
            actor_id=row.actor_id,
            feedback_type=FeedbackType(row.feedback_type),
            severity=FeedbackSeverity(row.severity),
            title=row.title,
            description=row.description,
            request_id=row.request_id,
            trace_id=row.trace_id,
            operation_log_id=row.operation_log_id,
            proposal_id=row.proposal_id,
            entity_id=row.entity_id,
            statement_id=row.statement_id,
            source_id=row.source_id,
            context=dict(row.context or {}),
            fingerprint=row.fingerprint,
            status=FeedbackStatus(row.status),
            resolution=row.resolution,
            resolved_by_actor_id=row.resolved_by_actor_id,
            resolved_at=row.resolved_at,
            occurrence_count=row.occurrence_count,
            last_seen_at=row.last_seen_at,
            created_at=row.created_at,
        )
