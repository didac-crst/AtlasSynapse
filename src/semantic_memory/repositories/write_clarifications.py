"""Persistence for write/identity clarification requests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from semantic_memory.models.enums import WriteClarificationStatus
from semantic_memory.models.operations import WriteClarificationRequest


class WriteClarificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, clarification_request_id: uuid.UUID) -> WriteClarificationRequest | None:
        return self._session.get(WriteClarificationRequest, clarification_request_id)

    def get_for_update(
        self, clarification_request_id: uuid.UUID
    ) -> WriteClarificationRequest | None:
        """Load with FOR UPDATE + populate_existing for one-shot answer races."""
        return self._session.get(
            WriteClarificationRequest,
            clarification_request_id,
            with_for_update=True,
            populate_existing=True,
        )

    def create(
        self,
        *,
        actor_id: uuid.UUID,
        operation_name: str,
        operation_mode: str,
        original_request_id: uuid.UUID,
        ambiguous_path: str,
        frozen_request: dict[str, Any],
        identity_snapshot: dict[str, Any],
        candidate_entity_ids: list[str],
        ttl_minutes: int,
        supersedes_clarification_request_id: uuid.UUID | None = None,
    ) -> WriteClarificationRequest:
        now = datetime.now(UTC)
        row = WriteClarificationRequest(
            id=uuid.uuid4(),
            actor_id=actor_id,
            operation_name=operation_name,
            operation_mode=operation_mode,
            original_request_id=original_request_id,
            ambiguous_path=ambiguous_path,
            frozen_request=frozen_request,
            identity_snapshot=identity_snapshot,
            candidate_entity_ids=candidate_entity_ids,
            status=WriteClarificationStatus.OPEN.value,
            expires_at=now + timedelta(minutes=ttl_minutes),
            supersedes_clarification_request_id=supersedes_clarification_request_id,
            created_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def mark_expired_if_needed(self, row: WriteClarificationRequest) -> WriteClarificationRequest:
        if row.status != WriteClarificationStatus.OPEN.value:
            return row
        if row.expires_at <= datetime.now(UTC):
            row.status = WriteClarificationStatus.EXPIRED.value
            self._session.flush()
        return row

    def supersede(self, row: WriteClarificationRequest) -> WriteClarificationRequest:
        row.status = WriteClarificationStatus.SUPERSEDED.value
        self._session.flush()
        return row

    def mark_resolved(
        self,
        row: WriteClarificationRequest,
        *,
        resolution: str,
        answered_by_actor_id: uuid.UUID,
        chosen_entity_id: uuid.UUID | None = None,
        resulting_request_id: uuid.UUID | None = None,
    ) -> WriteClarificationRequest:
        row.status = WriteClarificationStatus.RESOLVED.value
        row.resolution = resolution
        row.chosen_entity_id = chosen_entity_id
        row.answered_by_actor_id = answered_by_actor_id
        row.answered_at = datetime.now(UTC)
        row.resulting_request_id = resulting_request_id
        self._session.flush()
        return row

    def delete_older_than(self, *, older_than: datetime) -> int:
        rows = list(
            self._session.scalars(
                select(WriteClarificationRequest).where(
                    WriteClarificationRequest.created_at < older_than,
                    WriteClarificationRequest.status.in_(
                        [
                            WriteClarificationStatus.RESOLVED.value,
                            WriteClarificationStatus.EXPIRED.value,
                            WriteClarificationStatus.SUPERSEDED.value,
                        ]
                    ),
                )
            ).all()
        )
        if not rows:
            return 0
        ids = [row.id for row in rows]
        # Clear inbound self-FK refs (including rows outside this batch) before delete.
        self._session.execute(
            update(WriteClarificationRequest)
            .where(WriteClarificationRequest.supersedes_clarification_request_id.in_(ids))
            .values(supersedes_clarification_request_id=None)
        )
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return len(rows)
