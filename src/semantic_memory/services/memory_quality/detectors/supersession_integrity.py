"""Detect supersession structural integrity problems in a write neighborhood."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import Statement
from semantic_memory.models.enums import (
    MemoryQualityIssueType,
    MemoryQualitySeverity,
    QualityRouteOutcome,
    StatementStatus,
)
from semantic_memory.schemas.memory_quality import ChangeContext, QualityFinding

DETECTOR_KEY = "supersession_integrity"
DETECTOR_VERSION = "1"


class SupersessionIntegrityDetector:
    key = DETECTOR_KEY
    version = DETECTOR_VERSION

    def __init__(self, session: Session) -> None:
        self._session = session

    def inspect(self, change_context: ChangeContext) -> list[QualityFinding]:
        statement_ids = list(dict.fromkeys(change_context.touched_statement_ids))
        for prev, succ in change_context.supersession_edges:
            statement_ids.append(prev)
            statement_ids.append(succ)
        statement_ids = list(dict.fromkeys(statement_ids))
        if not statement_ids:
            return []

        # Bound neighborhood: seed statements + one hop of supersession pointers.
        seed = list(
            self._session.scalars(
                select(Statement).where(Statement.id.in_(statement_ids)).limit(50)
            ).all()
        )
        hop_ids: set[uuid.UUID] = set()
        for row in seed:
            if row.superseded_by_statement_id is not None:
                hop_ids.add(row.superseded_by_statement_id)
        if hop_ids:
            extra = list(
                self._session.scalars(
                    select(Statement).where(Statement.id.in_(hop_ids)).limit(50)
                ).all()
            )
            seed.extend(extra)

        by_id = {row.id: row for row in seed}
        findings: list[QualityFinding] = []
        seen: set[str] = set()

        for row in seed:
            for finding in self._check_row(row, by_id):
                key = (
                    f"{finding.issue_type}:{finding.statement_id}:"
                    f"{sorted(str(s) for s in finding.related_statement_ids)}"
                )
                if key in seen:
                    continue
                seen.add(key)
                findings.append(finding)
        return findings

    def _check_row(
        self, row: Statement, by_id: dict[uuid.UUID, Statement]
    ) -> list[QualityFinding]:
        out: list[QualityFinding] = []
        if row.status == StatementStatus.SUPERSEDED.value:
            if row.superseded_by_statement_id is None:
                out.append(
                    self._finding(
                        row,
                        related=[],
                        summary=(
                            f"Statement {row.id} is superseded but has no successor link."
                        ),
                        notes=["missing_successor"],
                        requires_clarification=True,
                    )
                )
            else:
                successor = by_id.get(row.superseded_by_statement_id)
                if successor is None:
                    successor = self._session.get(Statement, row.superseded_by_statement_id)
                    if successor is not None:
                        by_id[successor.id] = successor
                if successor is None:
                    out.append(
                        self._finding(
                            row,
                            related=[row.superseded_by_statement_id],
                            summary=(
                                f"Statement {row.id} points to missing successor "
                                f"{row.superseded_by_statement_id}."
                            ),
                            notes=["dangling_successor"],
                            requires_clarification=True,
                        )
                    )
                else:
                    if (
                        successor.subject_entity_id != row.subject_entity_id
                        or successor.predicate_id != row.predicate_id
                    ):
                        out.append(
                            self._finding(
                                row,
                                related=[successor.id],
                                summary=(
                                    f"Successor {successor.id} is semantically incompatible "
                                    f"with superseded statement {row.id}."
                                ),
                                notes=["incompatible_successor"],
                                requires_clarification=True,
                                severity=MemoryQualitySeverity.HIGH,
                            )
                        )
                    cycle = self._detect_cycle(row, by_id)
                    if cycle:
                        out.append(
                            self._finding(
                                row,
                                related=cycle,
                                summary=(
                                    f"Supersession cycle detected involving statement {row.id}."
                                ),
                                notes=["supersession_cycle"],
                                requires_clarification=True,
                                severity=MemoryQualitySeverity.CRITICAL,
                            )
                        )
        return out

    def _detect_cycle(
        self, start: Statement, by_id: dict[uuid.UUID, Statement]
    ) -> list[uuid.UUID]:
        seen: list[uuid.UUID] = []
        current: Statement | None = start
        for _ in range(32):
            if current is None or current.superseded_by_statement_id is None:
                return []
            nxt_id = current.superseded_by_statement_id
            if nxt_id in seen:
                return seen + [nxt_id]
            seen.append(nxt_id)
            nxt = by_id.get(nxt_id)
            if nxt is None:
                nxt = self._session.get(Statement, nxt_id)
                if nxt is not None:
                    by_id[nxt.id] = nxt
            current = nxt
        return seen

    def _finding(
        self,
        row: Statement,
        *,
        related: list[uuid.UUID],
        summary: str,
        notes: list[str],
        requires_clarification: bool,
        severity: MemoryQualitySeverity = MemoryQualitySeverity.HIGH,
    ) -> QualityFinding:
        related_ids = list(dict.fromkeys([row.id, *related]))
        return QualityFinding(
            issue_type=MemoryQualityIssueType.SUPERSESSION_INTEGRITY,
            severity=severity,
            detector_key=self.key,
            detector_version=self.version,
            summary=summary,
            evidence={
                "detector_key": self.key,
                "notes": notes,
                "statement_id": str(row.id),
                "superseded_by_statement_id": (
                    str(row.superseded_by_statement_id)
                    if row.superseded_by_statement_id
                    else None
                ),
            },
            subject_entity_id=row.subject_entity_id,
            statement_id=row.id,
            related_entity_ids=[row.subject_entity_id],
            related_statement_ids=related_ids,
            requires_clarification=requires_clarification,
            recommended_route=QualityRouteOutcome.OPEN_QUALITY_ISSUE,
        )
