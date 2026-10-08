"""Memory quality orchestration: inspect, route, upsert, surface warnings."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models.enums import (
    MemoryQualityIssueStatus,
    MemoryQualityIssueType,
    MemoryQualityResolution,
    MemoryQualitySeverity,
    QualityRouteOutcome,
)
from semantic_memory.models.memory_quality import MemoryQualityIssue
from semantic_memory.observability.logging import get_logger
from semantic_memory.repositories.memory_quality import MemoryQualityRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.memory_quality import (
    ChangeContext,
    MemoryQualityIssueListResponse,
    MemoryQualityIssueResponse,
    QualityFinding,
    QualityWarning,
)
from semantic_memory.services.conflicts import ConflictService
from semantic_memory.services.memory_quality.detectors.supersession_integrity import (
    SupersessionIntegrityDetector,
)
from semantic_memory.services.memory_quality.fingerprint import build_quality_fingerprint
from semantic_memory.services.memory_quality.routing import route_finding

logger = get_logger("semantic_memory.memory_quality")

QUALITY_WARNINGS_MAX = 5


class MemoryQualityService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._issues = MemoryQualityRepository(session)
        self._statements = StatementRepository(session)
        self._conflicts = ConflictService(session)
        self._detectors = [SupersessionIntegrityDetector(session)]

    def inspect_after_write(self, change_context: ChangeContext) -> list[QualityWarning]:
        """Run neighborhood detectors after a successful mutation.

        Uses a nested savepoint so detector/upsert failures cannot roll back the
        primary mutation body. Dry-run skips durable issue writes.
        """
        if change_context.dry_run:
            return []
        try:
            with self._session.begin_nested():
                return self._inspect_and_route(change_context)
        except Exception:
            logger.exception(
                "memory_quality_inspect_failed",
                extra={
                    "event": "memory_quality_inspect_failed",
                    "operation": change_context.operation_name,
                    "request_id": (
                        str(change_context.request_id)
                        if change_context.request_id
                        else None
                    ),
                },
            )
            return []

    def warnings_for_hits(
        self,
        *,
        entity_ids: list[uuid.UUID],
        statement_ids: list[uuid.UUID],
        limit: int = QUALITY_WARNINGS_MAX,
    ) -> list[QualityWarning]:
        rows = self._issues.list_open_for_entities_or_statements(
            entity_ids=entity_ids,
            statement_ids=statement_ids,
            limit=limit,
        )
        # Relevance filter: also match related_* JSON membership loosely.
        entity_set = {str(e) for e in entity_ids}
        statement_set = {str(s) for s in statement_ids}
        filtered: list[MemoryQualityIssue] = []
        for row in rows:
            if self._intersects(row, entity_set, statement_set):
                filtered.append(row)
        return [self._to_warning(row) for row in filtered[:limit]]

    def list_issues(
        self,
        *,
        status: str | None = None,
        issue_type: str | None = None,
        subject_entity_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> MemoryQualityIssueListResponse:
        rows, total = self._issues.list_issues(
            status=status,
            issue_type=issue_type,
            subject_entity_id=subject_entity_id,
            limit=limit,
            offset=offset,
        )
        return MemoryQualityIssueListResponse(
            items=[self._to_response(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_issue(self, issue_id: uuid.UUID) -> MemoryQualityIssueResponse | None:
        row = self._issues.get(issue_id)
        if row is None:
            return None
        return self._to_response(row)

    def _inspect_and_route(self, change_context: ChangeContext) -> list[QualityWarning]:
        # Competing facts remain owned exclusively by ConflictService (assert/supersede
        # paths). Quality never opens parallel conflict rows here.
        findings: list[QualityFinding] = []
        for detector in self._detectors:
            findings.extend(detector.inspect(change_context))

        warnings: list[QualityWarning] = []
        skip = set(change_context.skip_fingerprints)
        for finding in findings:
            outcome = route_finding(finding)
            if outcome == QualityRouteOutcome.IGNORE:
                continue
            if outcome == QualityRouteOutcome.UPSERT_CONFLICT:
                self._upsert_conflict(finding)
                continue
            if outcome == QualityRouteOutcome.AUTO_RESOLVE:
                # Deterministic fix would run here; checkpoint 1 has no auto-fix
                # for supersession integrity — treat as open if recommended wrongly.
                continue
            if outcome == QualityRouteOutcome.IDENTITY_REVIEW:
                outcome = QualityRouteOutcome.OPEN_QUALITY_ISSUE
            if outcome != QualityRouteOutcome.OPEN_QUALITY_ISSUE:
                continue

            fingerprint = build_quality_fingerprint(
                issue_type=finding.issue_type,
                detector_key=finding.detector_key,
                entity_ids=list(
                    {
                        e
                        for e in [
                            finding.subject_entity_id,
                            finding.object_entity_id,
                            *finding.related_entity_ids,
                        ]
                        if e is not None
                    }
                ),
                statement_ids=list(
                    {
                        s
                        for s in [finding.statement_id, *finding.related_statement_ids]
                        if s is not None
                    }
                ),
            )
            if fingerprint in skip:
                continue
            # UNKNOWN suppression (partial): skip reopen of same fingerprint if a
            # closed UNKNOWN exists and evidence is unchanged — deferred full
            # material-evidence compare; still avoid spam via open dedupe only here.
            row = self._upsert_issue(finding, fingerprint, change_context)
            if row is not None:
                warnings.append(self._to_warning(row))
        return warnings[:QUALITY_WARNINGS_MAX]

    def _upsert_conflict(self, finding: QualityFinding) -> None:
        if finding.conflict_new_statement_id is None:
            return
        new_stmt = self._statements.get(finding.conflict_new_statement_id)
        if new_stmt is None:
            return
        existing = [
            row
            for sid in finding.conflict_existing_statement_ids
            if (row := self._statements.get(sid)) is not None
        ]
        self._conflicts.record_cardinality_conflicts(
            new_statement=new_stmt,
            existing=existing,
            predicate_key=finding.conflict_predicate_key or "unknown",
        )

    def _upsert_issue(
        self,
        finding: QualityFinding,
        fingerprint: str,
        change_context: ChangeContext,
    ) -> MemoryQualityIssue | None:
        existing = self._issues.find_open_by_fingerprint(fingerprint)
        related_entities = [str(e) for e in finding.related_entity_ids]
        related_statements = [str(s) for s in finding.related_statement_ids]
        if existing is not None:
            return self._issues.touch_open(
                existing,
                evidence=finding.evidence,
                summary=finding.summary,
                severity=finding.severity.value,
                requires_clarification=finding.requires_clarification,
                detector_version=finding.detector_version,
                operation_id=change_context.operation_id,
            )
        return self._issues.create(
            issue_type=finding.issue_type.value,
            severity=finding.severity.value,
            detector_key=finding.detector_key,
            detector_version=finding.detector_version,
            fingerprint=fingerprint,
            summary=finding.summary,
            evidence=finding.evidence,
            requires_clarification=finding.requires_clarification,
            subject_entity_id=finding.subject_entity_id,
            object_entity_id=finding.object_entity_id,
            statement_id=finding.statement_id,
            related_entity_ids=related_entities,
            related_statement_ids=related_statements,
            trigger_operation_id=change_context.operation_id,
        )

    @staticmethod
    def _intersects(
        row: MemoryQualityIssue,
        entity_set: set[str],
        statement_set: set[str],
    ) -> bool:
        if row.subject_entity_id and str(row.subject_entity_id) in entity_set:
            return True
        if row.object_entity_id and str(row.object_entity_id) in entity_set:
            return True
        if row.statement_id and str(row.statement_id) in statement_set:
            return True
        for eid in row.related_entity_ids or []:
            if str(eid) in entity_set:
                return True
        for sid in row.related_statement_ids or []:
            if str(sid) in statement_set:
                return True
        return False

    def _to_warning(self, row: MemoryQualityIssue) -> QualityWarning:
        return QualityWarning(
            issue_id=row.id,
            type=MemoryQualityIssueType(row.issue_type),
            severity=MemoryQualitySeverity(row.severity),
            summary=row.summary,
            requires_clarification=bool(row.requires_clarification),
            related_entity_ids=_parse_uuid_list(row.related_entity_ids),
            related_statement_ids=_parse_uuid_list(row.related_statement_ids),
        )

    def _to_response(self, row: MemoryQualityIssue) -> MemoryQualityIssueResponse:
        return MemoryQualityIssueResponse(
            id=row.id,
            issue_type=MemoryQualityIssueType(row.issue_type),
            status=MemoryQualityIssueStatus(row.status),
            severity=MemoryQualitySeverity(row.severity),
            detector_key=row.detector_key,
            detector_version=row.detector_version,
            fingerprint=row.fingerprint,
            subject_entity_id=row.subject_entity_id,
            object_entity_id=row.object_entity_id,
            statement_id=row.statement_id,
            related_entity_ids=_parse_uuid_list(row.related_entity_ids),
            related_statement_ids=_parse_uuid_list(row.related_statement_ids),
            summary=row.summary,
            evidence=dict(row.evidence or {}),
            requires_clarification=bool(row.requires_clarification),
            occurrence_count=int(row.occurrence_count or 1),
            resolution=(
                MemoryQualityResolution(row.resolution) if row.resolution else None
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
            resolved_at=row.resolved_at,
        )


def _parse_uuid_list(values: list[Any] | None) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in values or []:
        try:
            out.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return out
