"""Persistence for agent feedback observations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models.enums import FeedbackStatus
from semantic_memory.models.feedback import AgentFeedback


class FeedbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, feedback_id: uuid.UUID) -> AgentFeedback | None:
        return self._session.get(AgentFeedback, feedback_id)

    def find_open_by_fingerprint(self, fingerprint: str) -> AgentFeedback | None:
        return self._session.scalar(
            select(AgentFeedback)
            .where(
                AgentFeedback.fingerprint == fingerprint,
                AgentFeedback.status == FeedbackStatus.OPEN.value,
            )
            .order_by(AgentFeedback.created_at.asc())
            .limit(1)
        )

    def list(
        self,
        *,
        status: str | None = None,
        feedback_type: str | None = None,
        actor_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[AgentFeedback]:
        stmt = select(AgentFeedback).order_by(
            AgentFeedback.last_seen_at.desc(),
            AgentFeedback.created_at.desc(),
        )
        if status is not None:
            stmt = stmt.where(AgentFeedback.status == status)
        if feedback_type is not None:
            stmt = stmt.where(AgentFeedback.feedback_type == feedback_type)
        if actor_id is not None:
            stmt = stmt.where(AgentFeedback.actor_id == actor_id)
        stmt = stmt.limit(max(1, min(limit, 200)))
        return list(self._session.scalars(stmt).all())

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
        row.occurrence_count += 1
        row.last_seen_at = datetime.now(UTC)
        self._session.flush()
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
