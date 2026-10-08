"""Governed supersession-edge repair for open memory-quality issues."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    InvalidStateTransitionError,
    UnknownMemoryQualityIssueError,
    UnknownStatementError,
    ValidationFailedError,
)
from semantic_memory.models import Statement
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import (
    MemoryQualityIssueStatus,
    MemoryQualityIssueType,
    MemoryQualityResolution,
    StatementStatus,
    SupersessionRepairOutcome,
)
from semantic_memory.repositories.memory_quality import MemoryQualityRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.dry_run import OperationMode
from semantic_memory.schemas.memory_quality import (
    ChangeContext,
    QualityFinding,
    RepairSupersessionIntegrityRequest,
    RepairSupersessionIntegrityResponse,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.memory_quality.detectors.supersession_integrity import (
    SupersessionIntegrityDetector,
)
from semantic_memory.services.memory_quality.service import MemoryQualityService
from semantic_memory.services.mutations import MutationRunner


class SupersessionIntegrityRepairService:
    """Narrow governed repair: replace a bad/missing supersession successor."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._issues = MemoryQualityRepository(session)
        self._statements = StatementRepository(session)
        self._mutations = MutationRunner(session)
        self._quality = MemoryQualityService(session)
        self._detector = SupersessionIntegrityDetector(session)

    def repair(
        self, request: RepairSupersessionIntegrityRequest
    ) -> RepairSupersessionIntegrityResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="repair_supersession_integrity",
            request=request,
            response_model=RepairSupersessionIntegrityResponse,
            constraint_name="memory_quality_repair",
            execute=lambda: self._repair_body(request=request, actor_id=actor.id),
            post_execute=lambda response, operation_id: self._post_repair_quality(
                response,
                actor_id=actor.id,
                operation_id=operation_id,
            ),
        )

    def _repair_body(
        self,
        *,
        request: RepairSupersessionIntegrityRequest,
        actor_id: uuid.UUID,
    ) -> RepairSupersessionIntegrityResponse:
        issue = self._issues.get(request.issue_id)
        if issue is None:
            raise UnknownMemoryQualityIssueError(
                f"Memory quality issue {request.issue_id} was not found",
                details={"issue_id": str(request.issue_id)},
                request_id=str(request.request_id),
            )
        if issue.status != MemoryQualityIssueStatus.OPEN.value:
            raise InvalidStateTransitionError(
                f"Memory quality issue {issue.id} is not open",
                details={"issue_id": str(issue.id), "status": issue.status},
                request_id=str(request.request_id),
            )
        if issue.issue_type != MemoryQualityIssueType.SUPERSESSION_INTEGRITY.value:
            raise ValidationFailedError(
                "Only supersession_integrity issues can be repaired by this operation",
                details={
                    "issue_id": str(issue.id),
                    "issue_type": issue.issue_type,
                },
                request_id=str(request.request_id),
            )
        if issue.statement_id is None:
            raise ValidationFailedError(
                "Issue has no statement_id to repair",
                details={"issue_id": str(issue.id)},
                request_id=str(request.request_id),
            )

        target = self._statements.get(issue.statement_id)
        if target is None:
            raise UnknownStatementError(
                f"Statement {issue.statement_id} was not found",
                details={"statement_id": str(issue.statement_id)},
                request_id=str(request.request_id),
            )

        findings = self._findings_for_statement(target.id)
        if not findings:
            # Stale: no graph mutation; post_execute closes on execute path.
            return RepairSupersessionIntegrityResponse(
                outcome=SupersessionRepairOutcome.NO_LONGER_APPLICABLE,
                issue_id=issue.id,
                statement_id=target.id,
                previous_successor_statement_id=target.superseded_by_statement_id,
                successor_statement_id=None,
                issue_resolved=not request.dry_run,
                resolution=(MemoryQualityResolution.NO_ACTION if not request.dry_run else None),
                request_id=request.request_id,
            )

        successor = self._statements.get(request.successor_statement_id)
        if successor is None:
            raise UnknownStatementError(
                f"Successor statement {request.successor_statement_id} was not found",
                details={"statement_id": str(request.successor_statement_id)},
                request_id=str(request.request_id),
            )

        self._validate_successor_edge(
            target=target,
            successor=successor,
            request_id=request.request_id,
        )

        previous_successor = target.superseded_by_statement_id
        if request.dry_run:
            return RepairSupersessionIntegrityResponse(
                outcome=SupersessionRepairOutcome.WOULD_REPAIR,
                issue_id=issue.id,
                statement_id=target.id,
                previous_successor_statement_id=previous_successor,
                successor_statement_id=successor.id,
                issue_resolved=False,
                resolution=None,
                request_id=request.request_id,
                dry_run=True,
                operation_mode=OperationMode.DRY_RUN,
                would_persist=True,
            )

        # Ensure status reflects superseded history linkage when repairing.
        if target.status != StatementStatus.SUPERSEDED.value:
            target.status = StatementStatus.SUPERSEDED.value
        target.superseded_by_statement_id = successor.id
        self._session.flush()

        remaining = self._findings_for_statement(target.id)
        if remaining:
            # Edge applied but problem remains — roll back by raising after restore.
            target.superseded_by_statement_id = previous_successor
            self._session.flush()
            raise ValidationFailedError(
                "Proposed supersession repair did not clear the integrity problem",
                details={
                    "issue_id": str(issue.id),
                    "statement_id": str(target.id),
                    "successor_statement_id": str(successor.id),
                    "remaining_notes": [
                        note
                        for finding in remaining
                        for note in (finding.evidence.get("notes") or [])
                    ],
                },
                request_id=str(request.request_id),
            )

        # Closing happens in post_execute with the real operation_id.
        return RepairSupersessionIntegrityResponse(
            outcome=SupersessionRepairOutcome.REPAIRED,
            issue_id=issue.id,
            statement_id=target.id,
            previous_successor_statement_id=previous_successor,
            successor_statement_id=successor.id,
            issue_resolved=True,
            resolution=MemoryQualityResolution.STRUCTURAL_REPAIR,
            request_id=request.request_id,
        )

    def _post_repair_quality(
        self,
        response: RepairSupersessionIntegrityResponse,
        *,
        actor_id: uuid.UUID,
        operation_id: uuid.UUID,
    ) -> RepairSupersessionIntegrityResponse:
        if response.outcome == SupersessionRepairOutcome.REPAIRED:
            issue = self._issues.get(response.issue_id)
            if issue is not None:
                self._quality.mark_resolved_after_repair(
                    issue=issue,
                    actor_id=actor_id,
                    operation_id=operation_id,
                    resolution=MemoryQualityResolution.STRUCTURAL_REPAIR,
                )
        elif (
            response.outcome == SupersessionRepairOutcome.NO_LONGER_APPLICABLE
            and response.issue_resolved
        ):
            issue = self._issues.get(response.issue_id)
            if issue is not None:
                self._quality.mark_resolved_after_repair(
                    issue=issue,
                    actor_id=actor_id,
                    operation_id=operation_id,
                    resolution=MemoryQualityResolution.NO_ACTION,
                )

        touched_statements = [
            sid
            for sid in [
                response.statement_id,
                response.successor_statement_id,
                response.previous_successor_statement_id,
            ]
            if sid is not None
        ]
        touched_entities: list[uuid.UUID] = []
        for sid in touched_statements:
            row = self._statements.get(sid)
            if row is not None:
                touched_entities.append(row.subject_entity_id)
                if row.object_entity_id is not None:
                    touched_entities.append(row.object_entity_id)
        warnings = self._quality.inspect_after_write(
            ChangeContext(
                actor_id=actor_id,
                operation_id=operation_id,
                operation_name="repair_supersession_integrity",
                request_id=response.request_id,
                touched_entity_ids=list(dict.fromkeys(touched_entities)),
                touched_statement_ids=list(dict.fromkeys(touched_statements)),
                supersession_edges=(
                    [(response.statement_id, response.successor_statement_id)]
                    if response.statement_id and response.successor_statement_id
                    else []
                ),
                dry_run=bool(response.dry_run),
            )
        )
        return response.model_copy(update={"quality_warnings": warnings})

    def _findings_for_statement(self, statement_id: uuid.UUID) -> list[QualityFinding]:
        findings = self._detector.inspect(ChangeContext(touched_statement_ids=[statement_id]))
        return [f for f in findings if f.statement_id == statement_id]

    def _validate_successor_edge(
        self,
        *,
        target: Statement,
        successor: Statement,
        request_id: uuid.UUID,
    ) -> None:
        if successor.id == target.id:
            raise ValidationFailedError(
                "Successor statement must differ from the target statement",
                details={
                    "statement_id": str(target.id),
                    "successor_statement_id": str(successor.id),
                },
                request_id=str(request_id),
            )
        if (
            successor.subject_entity_id != target.subject_entity_id
            or successor.predicate_id != target.predicate_id
        ):
            raise ValidationFailedError(
                "Successor is semantically incompatible (subject/predicate mismatch)",
                details={
                    "statement_id": str(target.id),
                    "successor_statement_id": str(successor.id),
                },
                request_id=str(request_id),
            )
        # Projected cycle check: walk from successor, reject if we reach target.
        seen: set[uuid.UUID] = set()
        current: Statement | None = successor
        for _ in range(64):
            if current is None:
                break
            if current.id == target.id:
                raise ValidationFailedError(
                    "Proposed supersession edge would create a cycle",
                    details={
                        "statement_id": str(target.id),
                        "successor_statement_id": str(successor.id),
                    },
                    request_id=str(request_id),
                )
            if current.id in seen:
                break
            seen.add(current.id)
            if current.superseded_by_statement_id is None:
                break
            current = self._statements.get(current.superseded_by_statement_id)
