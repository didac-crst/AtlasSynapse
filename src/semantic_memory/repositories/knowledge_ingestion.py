"""Persistence for governed knowledge-ingestion working state."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models.enums import (
    KnowledgeCandidateState,
    KnowledgeIngestionClarificationStatus,
    KnowledgeIngestionEffectStatus,
    KnowledgeIngestionMode,
    KnowledgeIngestionStatus,
)
from semantic_memory.models.knowledge_ingestion import (
    KnowledgeCandidate,
    KnowledgeCandidateDependency,
    KnowledgeIngestion,
    KnowledgeIngestionClarification,
    KnowledgeIngestionEffect,
)

_UNSET: Any = object()


def effect_idempotency_key(
    ingestion_id: uuid.UUID,
    candidate_id: uuid.UUID,
    effect_key: str,
) -> str:
    """Deterministic operation idempotency key for a resumable effect."""
    return f"{ingestion_id}:{candidate_id}:{effect_key}"


class KnowledgeIngestionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- ingestion ---

    def get(self, ingestion_id: uuid.UUID) -> KnowledgeIngestion | None:
        return self._session.get(KnowledgeIngestion, ingestion_id)

    def find_by_actor_idempotency(
        self, actor_id: uuid.UUID, idempotency_key: str
    ) -> KnowledgeIngestion | None:
        return self._session.scalar(
            select(KnowledgeIngestion).where(
                KnowledgeIngestion.actor_id == actor_id,
                KnowledgeIngestion.idempotency_key == idempotency_key,
            )
        )

    def create_ingestion(
        self,
        *,
        actor_id: uuid.UUID,
        source_id: uuid.UUID,
        source_hash: str,
        request_id: uuid.UUID,
        idempotency_key: str,
        pipeline_version: str,
        ontology_revision_marker: str,
        mode: str = KnowledgeIngestionMode.EXECUTE.value,
        status: str = KnowledgeIngestionStatus.ACCEPTED.value,
        source_content_revision_id: uuid.UUID | None = None,
        document_entity_id: uuid.UUID | None = None,
        extraction_model: str | None = None,
        extraction_prompt_version: str | None = None,
        budgets_json: dict[str, Any] | None = None,
        stats_json: dict[str, Any] | None = None,
        pause_reason: str | None = None,
    ) -> KnowledgeIngestion:
        row = KnowledgeIngestion(
            id=uuid.uuid4(),
            actor_id=actor_id,
            mode=mode,
            status=status,
            source_id=source_id,
            source_content_revision_id=source_content_revision_id,
            document_entity_id=document_entity_id,
            source_hash=source_hash,
            request_id=request_id,
            idempotency_key=idempotency_key,
            pipeline_version=pipeline_version,
            ontology_revision_marker=ontology_revision_marker,
            extraction_model=extraction_model,
            extraction_prompt_version=extraction_prompt_version,
            budgets_json=budgets_json or {},
            stats_json=stats_json or {},
            pause_reason=pause_reason,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update_ingestion(
        self,
        row: KnowledgeIngestion,
        *,
        status: str | None = None,
        pause_reason: Any = _UNSET,
        stats_json: dict[str, Any] | None = None,
        budgets_json: dict[str, Any] | None = None,
        completed_at: Any = _UNSET,
        source_content_revision_id: Any = _UNSET,
    ) -> KnowledgeIngestion:
        if status is not None:
            row.status = status
        if pause_reason is not _UNSET:
            row.pause_reason = pause_reason
        if stats_json is not None:
            row.stats_json = stats_json
        if budgets_json is not None:
            row.budgets_json = budgets_json
        if completed_at is not _UNSET:
            row.completed_at = completed_at
        if source_content_revision_id is not _UNSET:
            row.source_content_revision_id = source_content_revision_id
        self._session.flush()
        return row

    # --- candidates ---

    def get_candidate(self, candidate_id: uuid.UUID) -> KnowledgeCandidate | None:
        return self._session.get(KnowledgeCandidate, candidate_id)

    def find_candidate_by_key(
        self, ingestion_id: uuid.UUID, candidate_key: str
    ) -> KnowledgeCandidate | None:
        return self._session.scalar(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.ingestion_id == ingestion_id,
                KnowledgeCandidate.candidate_key == candidate_key,
            )
        )

    def list_candidates(self, ingestion_id: uuid.UUID) -> list[KnowledgeCandidate]:
        return list(
            self._session.scalars(
                select(KnowledgeCandidate)
                .where(KnowledgeCandidate.ingestion_id == ingestion_id)
                .order_by(KnowledgeCandidate.created_at, KnowledgeCandidate.candidate_key)
            ).all()
        )

    def create_candidate(
        self,
        *,
        ingestion_id: uuid.UUID,
        candidate_key: str,
        kind: str,
        polarity: str,
        epistemic_status: str,
        derivation: str,
        claim_payload: dict[str, Any] | None = None,
        claim_text: str | None = None,
        source_span: dict[str, Any] | None = None,
        source_context_path: list[Any] | None = None,
        state: str = KnowledgeCandidateState.EXTRACTED.value,
        blockers_json: list[Any] | None = None,
        attempt_count: int = 0,
        resolution_json: dict[str, Any] | None = None,
        committed_entity_id: uuid.UUID | None = None,
        committed_statement_ids: list[Any] | None = None,
    ) -> KnowledgeCandidate:
        row = KnowledgeCandidate(
            id=uuid.uuid4(),
            ingestion_id=ingestion_id,
            candidate_key=candidate_key,
            kind=kind,
            polarity=polarity,
            epistemic_status=epistemic_status,
            derivation=derivation,
            claim_text=claim_text,
            claim_payload=claim_payload or {},
            source_span=source_span or {},
            source_context_path=source_context_path or [],
            state=state,
            blockers_json=blockers_json or [],
            attempt_count=attempt_count,
            resolution_json=resolution_json or {},
            committed_entity_id=committed_entity_id,
            committed_statement_ids=committed_statement_ids or [],
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update_candidate(
        self,
        row: KnowledgeCandidate,
        *,
        state: str | None = None,
        epistemic_status: str | None = None,
        blockers_json: list[Any] | None = None,
        attempt_count: int | None = None,
        resolution_json: dict[str, Any] | None = None,
        committed_entity_id: Any = _UNSET,
        committed_statement_ids: list[Any] | None = None,
    ) -> KnowledgeCandidate:
        if state is not None:
            row.state = state
        if epistemic_status is not None:
            row.epistemic_status = epistemic_status
        if blockers_json is not None:
            row.blockers_json = blockers_json
        if attempt_count is not None:
            row.attempt_count = attempt_count
        if resolution_json is not None:
            row.resolution_json = resolution_json
        if committed_entity_id is not _UNSET:
            row.committed_entity_id = committed_entity_id
        if committed_statement_ids is not None:
            row.committed_statement_ids = committed_statement_ids
        self._session.flush()
        return row

    # --- dependencies ---

    def add_dependency(
        self,
        *,
        parent_candidate_id: uuid.UUID,
        child_candidate_id: uuid.UUID,
        dependency_kind: str,
    ) -> KnowledgeCandidateDependency:
        row = KnowledgeCandidateDependency(
            id=uuid.uuid4(),
            parent_candidate_id=parent_candidate_id,
            child_candidate_id=child_candidate_id,
            dependency_kind=dependency_kind,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_dependencies_for_ingestion(
        self, ingestion_id: uuid.UUID
    ) -> list[KnowledgeCandidateDependency]:
        return list(
            self._session.scalars(
                select(KnowledgeCandidateDependency)
                .join(
                    KnowledgeCandidate,
                    KnowledgeCandidate.id == KnowledgeCandidateDependency.parent_candidate_id,
                )
                .where(KnowledgeCandidate.ingestion_id == ingestion_id)
            ).all()
        )

    # --- clarifications ---

    def get_clarification(
        self, clarification_id: uuid.UUID
    ) -> KnowledgeIngestionClarification | None:
        return self._session.get(KnowledgeIngestionClarification, clarification_id)

    def create_clarification(
        self,
        *,
        ingestion_id: uuid.UUID,
        clarification_kind: str,
        question_payload: dict[str, Any] | None = None,
        root_candidate_id: uuid.UUID | None = None,
        status: str = KnowledgeIngestionClarificationStatus.OPEN.value,
        impact_blocked_count: int = 0,
        write_clarification_request_id: uuid.UUID | None = None,
        ontology_clarification_request_id: uuid.UUID | None = None,
        answer_payload: dict[str, Any] | None = None,
        answered_at: datetime | None = None,
    ) -> KnowledgeIngestionClarification:
        row = KnowledgeIngestionClarification(
            id=uuid.uuid4(),
            ingestion_id=ingestion_id,
            root_candidate_id=root_candidate_id,
            clarification_kind=clarification_kind,
            question_payload=question_payload or {},
            answer_payload=answer_payload,
            status=status,
            impact_blocked_count=impact_blocked_count,
            write_clarification_request_id=write_clarification_request_id,
            ontology_clarification_request_id=ontology_clarification_request_id,
            answered_at=answered_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update_clarification(
        self,
        row: KnowledgeIngestionClarification,
        *,
        status: str | None = None,
        answer_payload: dict[str, Any] | None = None,
        impact_blocked_count: int | None = None,
        write_clarification_request_id: Any = _UNSET,
        ontology_clarification_request_id: Any = _UNSET,
        answered_at: Any = _UNSET,
    ) -> KnowledgeIngestionClarification:
        if status is not None:
            row.status = status
        if answer_payload is not None:
            row.answer_payload = answer_payload
        if impact_blocked_count is not None:
            row.impact_blocked_count = impact_blocked_count
        if write_clarification_request_id is not _UNSET:
            row.write_clarification_request_id = write_clarification_request_id
        if ontology_clarification_request_id is not _UNSET:
            row.ontology_clarification_request_id = ontology_clarification_request_id
        if answered_at is not _UNSET:
            row.answered_at = answered_at
        self._session.flush()
        return row

    def list_clarifications(self, ingestion_id: uuid.UUID) -> list[KnowledgeIngestionClarification]:
        return list(
            self._session.scalars(
                select(KnowledgeIngestionClarification).where(
                    KnowledgeIngestionClarification.ingestion_id == ingestion_id
                )
            ).all()
        )

    # --- effects ---

    def get_effect(self, effect_id: uuid.UUID) -> KnowledgeIngestionEffect | None:
        return self._session.get(KnowledgeIngestionEffect, effect_id)

    def find_effect_by_key(
        self,
        ingestion_id: uuid.UUID,
        candidate_id: uuid.UUID,
        effect_key: str,
    ) -> KnowledgeIngestionEffect | None:
        return self._session.scalar(
            select(KnowledgeIngestionEffect).where(
                KnowledgeIngestionEffect.ingestion_id == ingestion_id,
                KnowledgeIngestionEffect.candidate_id == candidate_id,
                KnowledgeIngestionEffect.effect_key == effect_key,
            )
        )

    def list_effects(self, ingestion_id: uuid.UUID) -> list[KnowledgeIngestionEffect]:
        return list(
            self._session.scalars(
                select(KnowledgeIngestionEffect).where(
                    KnowledgeIngestionEffect.ingestion_id == ingestion_id
                )
            ).all()
        )

    def create_effect(
        self,
        *,
        ingestion_id: uuid.UUID,
        candidate_id: uuid.UUID,
        effect_key: str,
        effect_type: str,
        status: str = KnowledgeIngestionEffectStatus.FAILED.value,
        operation_id: uuid.UUID | None = None,
        statement_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        details_json: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        idempotency_key: str | None = None,
    ) -> KnowledgeIngestionEffect:
        row = KnowledgeIngestionEffect(
            id=uuid.uuid4(),
            ingestion_id=ingestion_id,
            candidate_id=candidate_id,
            effect_key=effect_key,
            effect_type=effect_type,
            status=status,
            idempotency_key=idempotency_key
            or effect_idempotency_key(ingestion_id, candidate_id, effect_key),
            operation_id=operation_id,
            statement_id=statement_id,
            entity_id=entity_id,
            details_json=details_json or {},
            error_code=error_code,
            error_message=error_message,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update_effect(
        self,
        row: KnowledgeIngestionEffect,
        *,
        status: str | None = None,
        operation_id: Any = _UNSET,
        statement_id: Any = _UNSET,
        entity_id: Any = _UNSET,
        details_json: dict[str, Any] | None = None,
        error_code: Any = _UNSET,
        error_message: Any = _UNSET,
    ) -> KnowledgeIngestionEffect:
        if status is not None:
            row.status = status
        if operation_id is not _UNSET:
            row.operation_id = operation_id
        if statement_id is not _UNSET:
            row.statement_id = statement_id
        if entity_id is not _UNSET:
            row.entity_id = entity_id
        if details_json is not None:
            row.details_json = details_json
        if error_code is not _UNSET:
            row.error_code = error_code
        if error_message is not _UNSET:
            row.error_message = error_message
        row.updated_at = datetime.now(UTC)
        self._session.flush()
        return row
