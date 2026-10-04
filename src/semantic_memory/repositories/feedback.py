"""Persistence for agent feedback observations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from semantic_memory.models.enums import FeedbackStatus
from semantic_memory.models.feedback import AgentFeedback


class FeedbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, feedback_id: uuid.UUID) -> AgentFeedback | None:
        return self._session.get(AgentFeedback, feedback_id)

    def find_open_by_fingerprint(
        self, fingerprint: str, *, for_update: bool = False
    ) -> AgentFeedback | None:
        stmt = (
            select(AgentFeedback)
            .where(
                AgentFeedback.fingerprint == fingerprint,
                AgentFeedback.status == FeedbackStatus.OPEN.value,
            )
            .order_by(AgentFeedback.created_at.asc())
            .limit(1)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self._session.scalar(stmt)

    def list(
        self,
        *,
        status: str | None = None,
        feedback_type: str | None = None,
        severity: str | None = None,
        actor_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        proposal_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        order_by_created: bool = False,
    ) -> list[AgentFeedback]:
        stmt = self._filtered_select(
            status=status,
            feedback_type=feedback_type,
            severity=severity,
            actor_id=actor_id,
            entity_id=entity_id,
            proposal_id=proposal_id,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        )
        if order_by_created:
            stmt = stmt.order_by(AgentFeedback.created_at.desc(), AgentFeedback.id.desc())
        else:
            stmt = stmt.order_by(
                AgentFeedback.last_seen_at.desc(),
                AgentFeedback.created_at.desc(),
                AgentFeedback.id.desc(),
            )
        return list(self._session.scalars(stmt.limit(limit).offset(offset)).all())

    def count(
        self,
        *,
        status: str | None = None,
        feedback_type: str | None = None,
        severity: str | None = None,
        actor_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        proposal_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        trace_id: uuid.UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        filtered = self._filtered_select(
            status=status,
            feedback_type=feedback_type,
            severity=severity,
            actor_id=actor_id,
            entity_id=entity_id,
            proposal_id=proposal_id,
            request_id=request_id,
            trace_id=trace_id,
            created_after=created_after,
            created_before=created_before,
        ).subquery()
        return int(self._session.scalar(select(func.count()).select_from(filtered)) or 0)

    def aggregate_summary(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        open_filter = self._time_filters(
            select(AgentFeedback).where(AgentFeedback.status == FeedbackStatus.OPEN.value),
            created_after=created_after,
            created_before=created_before,
        ).subquery()
        total_open = int(self._session.scalar(select(func.count()).select_from(open_filter)) or 0)

        by_severity_rows = self._session.execute(
            self._time_filters(
                select(AgentFeedback.severity, func.count()).where(
                    AgentFeedback.status == FeedbackStatus.OPEN.value
                ),
                created_after=created_after,
                created_before=created_before,
            ).group_by(AgentFeedback.severity)
        )
        by_type_rows = self._session.execute(
            self._time_filters(
                select(AgentFeedback.feedback_type, func.count()).where(
                    AgentFeedback.status == FeedbackStatus.OPEN.value
                ),
                created_after=created_after,
                created_before=created_before,
            ).group_by(AgentFeedback.feedback_type)
        )
        repeated = int(
            self._session.scalar(
                self._time_filters(
                    select(func.count()).where(
                        AgentFeedback.status == FeedbackStatus.OPEN.value,
                        AgentFeedback.occurrence_count > 1,
                    ),
                    created_after=created_after,
                    created_before=created_before,
                )
            )
            or 0
        )
        return {
            "total_open": total_open,
            "by_severity": {str(k): int(v) for k, v in by_severity_rows},
            "by_type": {str(k): int(v) for k, v in by_type_rows},
            "repeated_open_feedback": repeated,
        }

    def _filtered_select(
        self,
        *,
        status: str | None,
        feedback_type: str | None,
        severity: str | None,
        actor_id: uuid.UUID | None,
        entity_id: uuid.UUID | None,
        proposal_id: uuid.UUID | None,
        request_id: uuid.UUID | None,
        trace_id: uuid.UUID | None,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        stmt = select(AgentFeedback)
        if status is not None:
            stmt = stmt.where(AgentFeedback.status == status)
        if feedback_type is not None:
            stmt = stmt.where(AgentFeedback.feedback_type == feedback_type)
        if severity is not None:
            stmt = stmt.where(AgentFeedback.severity == severity)
        if actor_id is not None:
            stmt = stmt.where(AgentFeedback.actor_id == actor_id)
        if entity_id is not None:
            stmt = stmt.where(AgentFeedback.entity_id == entity_id)
        if proposal_id is not None:
            stmt = stmt.where(AgentFeedback.proposal_id == proposal_id)
        if request_id is not None:
            stmt = stmt.where(AgentFeedback.request_id == request_id)
        if trace_id is not None:
            stmt = stmt.where(AgentFeedback.trace_id == trace_id)
        return self._time_filters(stmt, created_after=created_after, created_before=created_before)

    @staticmethod
    def _time_filters(
        stmt: Any,
        *,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> Any:
        if created_after is not None:
            stmt = stmt.where(AgentFeedback.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(AgentFeedback.created_at <= created_before)
        return stmt

    def create(
        self,
        *,
        actor_id: uuid.UUID,
        feedback_type: str,
        severity: str,
        title: str,
        description: str,
        request_id: uuid.UUID | None,
        trace_id: uuid.UUID | None,
        operation_log_id: uuid.UUID | None,
        proposal_id: uuid.UUID | None,
        entity_id: uuid.UUID | None,
        statement_id: uuid.UUID | None,
        source_id: uuid.UUID | None,
        context: dict[str, Any],
        fingerprint: str | None,
    ) -> AgentFeedback:
        now = datetime.now(UTC)
        row = AgentFeedback(
            id=uuid.uuid4(),
            actor_id=actor_id,
            feedback_type=feedback_type,
            severity=severity,
            title=title,
            description=description,
            request_id=request_id,
            trace_id=trace_id,
            operation_log_id=operation_log_id,
            proposal_id=proposal_id,
            entity_id=entity_id,
            statement_id=statement_id,
            source_id=source_id,
            context=context,
            fingerprint=fingerprint,
            status=FeedbackStatus.OPEN.value,
            occurrence_count=1,
            last_seen_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def touch_occurrence(self, row: AgentFeedback) -> AgentFeedback:
        now = datetime.now(UTC)
        self._session.execute(
            update(AgentFeedback)
            .where(AgentFeedback.id == row.id)
            .values(
                occurrence_count=AgentFeedback.occurrence_count + 1,
                last_seen_at=now,
            )
        )
        self._session.refresh(row)
        return row

    def resolve(
        self,
        row: AgentFeedback,
        *,
        status: str,
        resolution: str | None,
        resolved_by_actor_id: uuid.UUID,
    ) -> AgentFeedback:
        row.status = status
        row.resolution = resolution
        row.resolved_by_actor_id = resolved_by_actor_id
        row.resolved_at = datetime.now(UTC)
        self._session.flush()
        return row
