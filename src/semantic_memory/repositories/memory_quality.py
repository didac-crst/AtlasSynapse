"""Persistence for memory quality issues."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from semantic_memory.models.enums import MemoryQualityIssueStatus
from semantic_memory.models.memory_quality import MemoryQualityIssue

_SEVERITY_RANK = case(
    (MemoryQualityIssue.severity == "critical", 5),
    (MemoryQualityIssue.severity == "high", 4),
    (MemoryQualityIssue.severity == "medium", 3),
    (MemoryQualityIssue.severity == "low", 2),
    (MemoryQualityIssue.severity == "info", 1),
    else_=0,
)


class MemoryQualityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, issue_id: uuid.UUID) -> MemoryQualityIssue | None:
        return self._session.get(MemoryQualityIssue, issue_id)

    def find_open_by_fingerprint(self, fingerprint: str) -> MemoryQualityIssue | None:
        return self._session.scalar(
            select(MemoryQualityIssue).where(
                MemoryQualityIssue.fingerprint == fingerprint,
                MemoryQualityIssue.status == MemoryQualityIssueStatus.OPEN.value,
            )
        )

    def create(
        self,
        *,
        issue_type: str,
        severity: str,
        detector_key: str,
        detector_version: str,
        fingerprint: str,
        summary: str,
        evidence: dict[str, Any],
        requires_clarification: bool,
        subject_entity_id: uuid.UUID | None = None,
        object_entity_id: uuid.UUID | None = None,
        statement_id: uuid.UUID | None = None,
        related_entity_ids: list[str] | None = None,
        related_statement_ids: list[str] | None = None,
        trigger_operation_id: uuid.UUID | None = None,
    ) -> MemoryQualityIssue:
        row = MemoryQualityIssue(
            id=uuid.uuid4(),
            issue_type=issue_type,
            status=MemoryQualityIssueStatus.OPEN.value,
            severity=severity,
            detector_key=detector_key,
            detector_version=detector_version,
            fingerprint=fingerprint,
            subject_entity_id=subject_entity_id,
            object_entity_id=object_entity_id,
            statement_id=statement_id,
            related_entity_ids=related_entity_ids or [],
            related_statement_ids=related_statement_ids or [],
            summary=summary,
            evidence=evidence,
            requires_clarification=requires_clarification,
            trigger_operation_id=trigger_operation_id,
            last_seen_operation_id=trigger_operation_id,
            occurrence_count=1,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def touch_open(
        self,
        row: MemoryQualityIssue,
        *,
        evidence: dict[str, Any],
        summary: str,
        severity: str,
        requires_clarification: bool,
        detector_version: str,
        operation_id: uuid.UUID | None,
    ) -> MemoryQualityIssue:
        row.evidence = evidence
        row.summary = summary
        row.severity = severity
        row.requires_clarification = requires_clarification
        row.detector_version = detector_version
        row.occurrence_count = int(row.occurrence_count or 1) + 1
        row.last_seen_operation_id = operation_id
        row.updated_at = datetime.now(UTC)
        self._session.flush()
        return row

    def list_open_for_entities_or_statements(
        self,
        *,
        entity_ids: list[uuid.UUID],
        statement_ids: list[uuid.UUID],
        limit: int = 5,
    ) -> list[MemoryQualityIssue]:
        if not entity_ids and not statement_ids:
            return []
        clauses: list[Any] = []
        if entity_ids:
            clauses.append(MemoryQualityIssue.subject_entity_id.in_(entity_ids))
            clauses.append(MemoryQualityIssue.object_entity_id.in_(entity_ids))
            for eid in entity_ids:
                # JSONB array membership for related_entity_ids (string UUIDs).
                clauses.append(MemoryQualityIssue.related_entity_ids.contains([str(eid)]))
        if statement_ids:
            clauses.append(MemoryQualityIssue.statement_id.in_(statement_ids))
            for sid in statement_ids:
                clauses.append(MemoryQualityIssue.related_statement_ids.contains([str(sid)]))
        stmt = (
            select(MemoryQualityIssue)
            .where(
                MemoryQualityIssue.status == MemoryQualityIssueStatus.OPEN.value,
                or_(*clauses),
            )
            .order_by(
                _SEVERITY_RANK.desc(),
                MemoryQualityIssue.updated_at.desc(),
                MemoryQualityIssue.id.desc(),
            )
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def list_issues(
        self,
        *,
        status: str | None = None,
        issue_type: str | None = None,
        subject_entity_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MemoryQualityIssue], int]:
        filters = []
        if status is not None:
            filters.append(MemoryQualityIssue.status == status)
        if issue_type is not None:
            filters.append(MemoryQualityIssue.issue_type == issue_type)
        if subject_entity_id is not None:
            filters.append(MemoryQualityIssue.subject_entity_id == subject_entity_id)
        count_stmt = select(func.count()).select_from(MemoryQualityIssue)
        list_stmt = select(MemoryQualityIssue)
        for f in filters:
            count_stmt = count_stmt.where(f)
            list_stmt = list_stmt.where(f)
        total = int(self._session.scalar(count_stmt) or 0)
        rows = list(
            self._session.scalars(
                list_stmt.order_by(
                    MemoryQualityIssue.updated_at.desc(),
                    MemoryQualityIssue.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return rows, total
