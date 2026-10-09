"""Persistence API for knowledge-ingestion working state (Phases B–D).

Extraction and resolution orchestration live in sibling packages; commit is Phase F.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    IdempotencyKeyReusedError,
    InvalidStateTransitionError,
    UnknownKnowledgeCandidateError,
    UnknownKnowledgeIngestionError,
    ValidationFailedError,
)
from semantic_memory.models.enums import (
    KnowledgeCandidateDependencyKind,
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    KnowledgeCandidateState,
    KnowledgeIngestionClarificationKind,
    KnowledgeIngestionClarificationStatus,
    KnowledgeIngestionEffectStatus,
    KnowledgeIngestionEffectType,
    KnowledgeIngestionMode,
    KnowledgeIngestionPauseReason,
    KnowledgeIngestionStatus,
)
from semantic_memory.models.knowledge_ingestion import (
    KnowledgeCandidate,
    KnowledgeCandidateDependency,
    KnowledgeIngestion,
    KnowledgeIngestionClarification,
    KnowledgeIngestionEffect,
)
from semantic_memory.repositories.knowledge_ingestion import (
    KnowledgeIngestionRepository,
    effect_idempotency_key,
)
from semantic_memory.repositories.provenance import ProvenanceRepository
from semantic_memory.repositories.write_clarifications import WriteClarificationRepository

_TERMINAL_INGESTION = frozenset(
    {
        KnowledgeIngestionStatus.COMPLETED.value,
        KnowledgeIngestionStatus.FAILED.value,
    }
)


class KnowledgeIngestionService:
    """Internal working-state persistence surface for governed ingestion."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = KnowledgeIngestionRepository(session)
        self._provenance = ProvenanceRepository(session)
        self._write_clarifications = WriteClarificationRepository(session)

    # --- ingestion ---

    def get_ingestion(self, ingestion_id: uuid.UUID) -> KnowledgeIngestion:
        row = self._repo.get(ingestion_id)
        if row is None:
            raise UnknownKnowledgeIngestionError(
                f"Unknown knowledge ingestion: {ingestion_id}",
                details={"ingestion_id": str(ingestion_id)},
            )
        return row

    def find_by_idempotency(
        self, actor_id: uuid.UUID, idempotency_key: str
    ) -> KnowledgeIngestion | None:
        return self._repo.find_by_actor_idempotency(actor_id, idempotency_key)

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
        mode: KnowledgeIngestionMode | str = KnowledgeIngestionMode.EXECUTE,
        source_content_revision_id: uuid.UUID | None = None,
        document_entity_id: uuid.UUID | None = None,
        extraction_model: str | None = None,
        extraction_prompt_version: str | None = None,
        budgets_json: dict[str, Any] | None = None,
        stats_json: dict[str, Any] | None = None,
    ) -> KnowledgeIngestion:
        mode_value = str(mode)
        if mode_value not in {m.value for m in KnowledgeIngestionMode}:
            raise ValidationFailedError(
                f"Invalid ingestion mode: {mode_value}",
                details={"mode": mode_value},
            )
        existing = self._repo.find_by_actor_idempotency(actor_id, idempotency_key)
        if existing is not None:
            raise IdempotencyKeyReusedError(
                "Knowledge ingestion idempotency key already used for this actor",
                details={
                    "actor_id": str(actor_id),
                    "idempotency_key": idempotency_key,
                    "existing_ingestion_id": str(existing.id),
                },
            )
        try:
            with self._session.begin_nested():
                return self._repo.create_ingestion(
                    actor_id=actor_id,
                    source_id=source_id,
                    source_hash=source_hash,
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                    pipeline_version=pipeline_version,
                    ontology_revision_marker=ontology_revision_marker,
                    mode=mode_value,
                    source_content_revision_id=source_content_revision_id,
                    document_entity_id=document_entity_id,
                    extraction_model=extraction_model,
                    extraction_prompt_version=extraction_prompt_version,
                    budgets_json=budgets_json,
                    stats_json=stats_json,
                )
        except IntegrityError as exc:
            raise IdempotencyKeyReusedError(
                "Knowledge ingestion idempotency key already used for this actor",
                details={
                    "actor_id": str(actor_id),
                    "idempotency_key": idempotency_key,
                },
            ) from exc

    def set_ingestion_status(
        self,
        ingestion_id: uuid.UUID,
        status: KnowledgeIngestionStatus | str,
        *,
        pause_reason: KnowledgeIngestionPauseReason | str | None = None,
        stats_json: dict[str, Any] | None = None,
    ) -> KnowledgeIngestion:
        row = self.get_ingestion(ingestion_id)
        status_value = str(status)
        if status_value not in {s.value for s in KnowledgeIngestionStatus}:
            raise ValidationFailedError(
                f"Invalid ingestion status: {status_value}",
                details={"status": status_value},
            )
        if (
            row.mode == KnowledgeIngestionMode.DRY_RUN.value
            and status_value == KnowledgeIngestionStatus.COMMITTING.value
        ):
            raise InvalidStateTransitionError(
                "Dry-run ingestion may never transition into committing",
                details={
                    "ingestion_id": str(ingestion_id),
                    "mode": row.mode,
                    "status": status_value,
                },
            )

        reason_value: str | None = None
        if status_value == KnowledgeIngestionStatus.PAUSED.value:
            reason_value = (
                str(pause_reason)
                if pause_reason is not None
                else KnowledgeIngestionPauseReason.BUDGET_EXHAUSTED.value
            )
            if reason_value not in {r.value for r in KnowledgeIngestionPauseReason}:
                raise ValidationFailedError(
                    f"Invalid pause_reason: {reason_value}",
                    details={"pause_reason": reason_value},
                )
        else:
            reason_value = None

        completed_at: datetime | None
        if status_value in _TERMINAL_INGESTION:
            completed_at = datetime.now(UTC)
        else:
            completed_at = None

        return self._repo.update_ingestion(
            row,
            status=status_value,
            pause_reason=reason_value,
            stats_json=stats_json,
            completed_at=completed_at,
        )

    def mark_paused_budget_exhausted(
        self, ingestion_id: uuid.UUID, *, stats_json: dict[str, Any] | None = None
    ) -> KnowledgeIngestion:
        return self.set_ingestion_status(
            ingestion_id,
            KnowledgeIngestionStatus.PAUSED,
            pause_reason=KnowledgeIngestionPauseReason.BUDGET_EXHAUSTED,
            stats_json=stats_json,
        )

    def mark_completed(
        self, ingestion_id: uuid.UUID, *, stats_json: dict[str, Any] | None = None
    ) -> KnowledgeIngestion:
        return self.set_ingestion_status(
            ingestion_id,
            KnowledgeIngestionStatus.COMPLETED,
            stats_json=stats_json,
        )

    def attach_source_content_revision(
        self,
        ingestion_id: uuid.UUID,
        source_content_revision_id: uuid.UUID,
    ) -> KnowledgeIngestion:
        """Fix the durable source revision for Claim occurrence identity.

        Once set, the revision cannot be changed. Candidates require a fixed
        revision before creation.
        """
        row = self.get_ingestion(ingestion_id)
        if row.source_content_revision_id is not None:
            if row.source_content_revision_id == source_content_revision_id:
                return row
            raise InvalidStateTransitionError(
                "source_content_revision_id is already fixed for this ingestion",
                details={
                    "ingestion_id": str(ingestion_id),
                    "existing_source_content_revision_id": str(row.source_content_revision_id),
                    "requested_source_content_revision_id": str(source_content_revision_id),
                },
            )
        revision = self._provenance.get_content_revision(source_content_revision_id)
        if revision is None:
            raise ValidationFailedError(
                "Unknown source_content_revision_id",
                details={"source_content_revision_id": str(source_content_revision_id)},
            )
        if revision.source_id != row.source_id:
            raise ValidationFailedError(
                "source_content_revision_id must belong to the ingestion source",
                details={
                    "ingestion_id": str(ingestion_id),
                    "source_id": str(row.source_id),
                    "revision_source_id": str(revision.source_id),
                    "source_content_revision_id": str(source_content_revision_id),
                },
            )
        return self._repo.update_ingestion(
            row, source_content_revision_id=source_content_revision_id
        )

    # --- candidates ---

    def get_candidate(self, candidate_id: uuid.UUID) -> KnowledgeCandidate:
        row = self._repo.get_candidate(candidate_id)
        if row is None:
            raise UnknownKnowledgeCandidateError(
                f"Unknown knowledge candidate: {candidate_id}",
                details={"candidate_id": str(candidate_id)},
            )
        return row

    def list_candidates(self, ingestion_id: uuid.UUID) -> list[KnowledgeCandidate]:
        self.get_ingestion(ingestion_id)
        return self._repo.list_candidates(ingestion_id)

    def _require_candidate_on_ingestion(
        self, ingestion_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> KnowledgeCandidate:
        candidate = self.get_candidate(candidate_id)
        if candidate.ingestion_id != ingestion_id:
            raise ValidationFailedError(
                "candidate_id must belong to ingestion_id",
                details={
                    "ingestion_id": str(ingestion_id),
                    "candidate_id": str(candidate_id),
                    "candidate_ingestion_id": str(candidate.ingestion_id),
                },
            )
        return candidate

    def create_candidate(
        self,
        *,
        ingestion_id: uuid.UUID,
        candidate_key: str,
        kind: KnowledgeCandidateKind | str,
        polarity: KnowledgeCandidatePolarity | str,
        epistemic_status: KnowledgeCandidateEpistemicStatus | str,
        derivation: KnowledgeCandidateDerivation | str,
        claim_payload: dict[str, Any] | None = None,
        claim_text: str | None = None,
        source_span: dict[str, Any] | None = None,
        source_context_path: list[Any] | None = None,
        state: KnowledgeCandidateState | str = KnowledgeCandidateState.EXTRACTED,
        blockers_json: list[Any] | None = None,
    ) -> KnowledgeCandidate:
        ingestion = self.get_ingestion(ingestion_id)
        if ingestion.source_content_revision_id is None:
            raise ValidationFailedError(
                "Candidates require a fixed source_content_revision_id on the ingestion",
                details={"ingestion_id": str(ingestion_id)},
            )
        kind_value = str(kind)
        polarity_value = str(polarity)
        epistemic_value = str(epistemic_status)
        derivation_value = str(derivation)
        state_value = str(state)
        for label, value, enum_cls in (
            ("kind", kind_value, KnowledgeCandidateKind),
            ("polarity", polarity_value, KnowledgeCandidatePolarity),
            ("epistemic_status", epistemic_value, KnowledgeCandidateEpistemicStatus),
            ("derivation", derivation_value, KnowledgeCandidateDerivation),
            ("state", state_value, KnowledgeCandidateState),
        ):
            if value not in {m.value for m in enum_cls}:
                raise ValidationFailedError(
                    f"Invalid candidate {label}: {value}",
                    details={label: value},
                )
        try:
            with self._session.begin_nested():
                return self._repo.create_candidate(
                    ingestion_id=ingestion_id,
                    candidate_key=candidate_key,
                    kind=kind_value,
                    polarity=polarity_value,
                    epistemic_status=epistemic_value,
                    derivation=derivation_value,
                    claim_payload=claim_payload,
                    claim_text=claim_text,
                    source_span=source_span,
                    source_context_path=source_context_path,
                    state=state_value,
                    blockers_json=blockers_json,
                )
        except IntegrityError as exc:
            raise ValidationFailedError(
                "candidate_key must be unique within an ingestion",
                details={
                    "ingestion_id": str(ingestion_id),
                    "candidate_key": candidate_key,
                },
            ) from exc

    def update_candidate_epistemic_status(
        self,
        candidate_id: uuid.UUID,
        epistemic_status: KnowledgeCandidateEpistemicStatus | str,
    ) -> KnowledgeCandidate:
        row = self.get_candidate(candidate_id)
        value = str(epistemic_status)
        if value not in {m.value for m in KnowledgeCandidateEpistemicStatus}:
            raise ValidationFailedError(
                f"Invalid epistemic_status: {value}",
                details={"epistemic_status": value},
            )
        return self._repo.update_candidate(row, epistemic_status=value)

    def update_candidate_state(
        self,
        candidate_id: uuid.UUID,
        state: KnowledgeCandidateState | str,
        *,
        blockers_json: list[Any] | None = None,
    ) -> KnowledgeCandidate:
        row = self.get_candidate(candidate_id)
        value = str(state)
        if value not in {m.value for m in KnowledgeCandidateState}:
            raise ValidationFailedError(
                f"Invalid candidate state: {value}",
                details={"state": value},
            )
        return self._repo.update_candidate(row, state=value, blockers_json=blockers_json)

    def persist_candidate_resolution(
        self,
        candidate_id: uuid.UUID,
        *,
        state: KnowledgeCandidateState | str,
        resolution: Any,
        blockers: list[Any] | None = None,
        attempt_count: int | None = None,
    ) -> KnowledgeCandidate:
        """Persist a typed Phase D resolution plan + blockers + processing state."""
        row = self.get_candidate(candidate_id)
        value = str(state)
        if value not in {m.value for m in KnowledgeCandidateState}:
            raise ValidationFailedError(
                f"Invalid candidate state: {value}",
                details={"state": value},
            )
        if hasattr(resolution, "model_dump"):
            resolution_json = resolution.model_dump(mode="json")
        elif isinstance(resolution, dict):
            resolution_json = resolution
        else:
            raise ValidationFailedError(
                "resolution must be a Pydantic model or dict",
                details={"candidate_id": str(candidate_id)},
            )
        blockers_json: list[Any] = []
        for item in blockers or []:
            if hasattr(item, "model_dump"):
                blockers_json.append(item.model_dump(mode="json"))
            elif isinstance(item, dict):
                blockers_json.append(item)
            else:
                raise ValidationFailedError(
                    "blockers must be Pydantic models or dicts",
                    details={"candidate_id": str(candidate_id)},
                )
        return self._repo.update_candidate(
            row,
            state=value,
            resolution_json=resolution_json,
            blockers_json=blockers_json,
            attempt_count=attempt_count,
        )

    # --- dependencies ---

    def add_dependency(
        self,
        *,
        parent_candidate_id: uuid.UUID,
        child_candidate_id: uuid.UUID,
        dependency_kind: KnowledgeCandidateDependencyKind | str = (
            KnowledgeCandidateDependencyKind.GENERIC
        ),
    ) -> KnowledgeCandidateDependency:
        """Record that ``child`` waits on ``parent`` (parent is the prerequisite)."""
        if parent_candidate_id == child_candidate_id:
            raise ValidationFailedError(
                "Self-dependency is not allowed",
                details={
                    "parent_candidate_id": str(parent_candidate_id),
                    "child_candidate_id": str(child_candidate_id),
                },
            )
        kind_value = str(dependency_kind)
        if kind_value not in {m.value for m in KnowledgeCandidateDependencyKind}:
            raise ValidationFailedError(
                f"Invalid dependency_kind: {kind_value}",
                details={"dependency_kind": kind_value},
            )
        parent = self.get_candidate(parent_candidate_id)
        child = self.get_candidate(child_candidate_id)
        if parent.ingestion_id != child.ingestion_id:
            raise ValidationFailedError(
                "Dependency endpoints must belong to the same ingestion",
                details={
                    "parent_ingestion_id": str(parent.ingestion_id),
                    "child_ingestion_id": str(child.ingestion_id),
                },
            )
        try:
            with self._session.begin_nested():
                return self._repo.add_dependency(
                    parent_candidate_id=parent_candidate_id,
                    child_candidate_id=child_candidate_id,
                    dependency_kind=kind_value,
                )
        except IntegrityError as exc:
            raise ValidationFailedError(
                "Duplicate candidate dependency edge",
                details={
                    "parent_candidate_id": str(parent_candidate_id),
                    "child_candidate_id": str(child_candidate_id),
                    "dependency_kind": kind_value,
                },
            ) from exc

    def list_dependencies(self, ingestion_id: uuid.UUID) -> list[KnowledgeCandidateDependency]:
        self.get_ingestion(ingestion_id)
        return self._repo.list_dependencies_for_ingestion(ingestion_id)

    # --- clarifications ---

    def record_clarification(
        self,
        *,
        ingestion_id: uuid.UUID,
        clarification_key: str,
        clarification_kind: KnowledgeIngestionClarificationKind | str,
        question_payload: dict[str, Any] | None = None,
        root_candidate_id: uuid.UUID | None = None,
        impact_blocked_count: int = 0,
        write_clarification_request_id: uuid.UUID | None = None,
        ontology_clarification_request_id: uuid.UUID | None = None,
        supersedes_clarification_id: uuid.UUID | None = None,
    ) -> KnowledgeIngestionClarification:
        self.get_ingestion(ingestion_id)
        key = clarification_key.strip()
        if not key:
            raise ValidationFailedError(
                "clarification_key must not be blank",
                details={"ingestion_id": str(ingestion_id)},
            )
        kind_value = str(clarification_kind)
        if kind_value not in {m.value for m in KnowledgeIngestionClarificationKind}:
            raise ValidationFailedError(
                f"Invalid clarification_kind: {kind_value}",
                details={"clarification_kind": kind_value},
            )
        if root_candidate_id is not None:
            root = self.get_candidate(root_candidate_id)
            if root.ingestion_id != ingestion_id:
                raise ValidationFailedError(
                    "root_candidate_id must belong to the same ingestion",
                    details={
                        "ingestion_id": str(ingestion_id),
                        "root_candidate_id": str(root_candidate_id),
                    },
                )
        if write_clarification_request_id is not None:
            handle = self._write_clarifications.get(write_clarification_request_id)
            if handle is None:
                raise ValidationFailedError(
                    "write_clarification_request_id does not exist",
                    details={
                        "write_clarification_request_id": str(write_clarification_request_id),
                    },
                )
        if supersedes_clarification_id is not None:
            prior = self._repo.get_clarification(supersedes_clarification_id)
            if prior is None or prior.ingestion_id != ingestion_id:
                raise ValidationFailedError(
                    "supersedes_clarification_id must belong to the same ingestion",
                    details={
                        "ingestion_id": str(ingestion_id),
                        "supersedes_clarification_id": str(supersedes_clarification_id),
                    },
                )
        existing = self._repo.find_clarification_by_key(ingestion_id, key)
        if existing is not None:
            return self._repo.update_clarification(
                existing,
                impact_blocked_count=impact_blocked_count,
                question_payload=question_payload or existing.question_payload,
            )
        try:
            with self._session.begin_nested():
                return self._repo.create_clarification(
                    ingestion_id=ingestion_id,
                    clarification_key=key,
                    clarification_kind=kind_value,
                    question_payload=question_payload,
                    root_candidate_id=root_candidate_id,
                    impact_blocked_count=impact_blocked_count,
                    write_clarification_request_id=write_clarification_request_id,
                    ontology_clarification_request_id=ontology_clarification_request_id,
                    supersedes_clarification_id=supersedes_clarification_id,
                )
        except IntegrityError as exc:
            raise ValidationFailedError(
                "clarification_key must be unique within an ingestion",
                details={
                    "ingestion_id": str(ingestion_id),
                    "clarification_key": key,
                },
            ) from exc

    def answer_clarification(
        self,
        clarification_id: uuid.UUID,
        *,
        answer_payload: dict[str, Any],
    ) -> KnowledgeIngestionClarification:
        row = self._repo.get_clarification(clarification_id)
        if row is None:
            raise ValidationFailedError(
                f"Unknown ingestion clarification: {clarification_id}",
                details={"clarification_id": str(clarification_id)},
            )
        if row.status == KnowledgeIngestionClarificationStatus.ANSWERED.value:
            if row.answer_payload == answer_payload:
                return row  # idempotent replay
            raise InvalidStateTransitionError(
                "Clarification already answered with a different payload",
                details={
                    "clarification_id": str(clarification_id),
                    "status": row.status,
                },
            )
        if row.status != KnowledgeIngestionClarificationStatus.OPEN.value:
            raise InvalidStateTransitionError(
                "Clarification is not open for answering",
                details={
                    "clarification_id": str(clarification_id),
                    "status": row.status,
                },
            )
        return self._repo.update_clarification(
            row,
            status=KnowledgeIngestionClarificationStatus.ANSWERED.value,
            answer_payload=answer_payload,
            answered_at=datetime.now(UTC),
        )

    def supersede_clarification(
        self, clarification_id: uuid.UUID
    ) -> KnowledgeIngestionClarification:
        row = self._repo.get_clarification(clarification_id)
        if row is None:
            raise ValidationFailedError(
                f"Unknown ingestion clarification: {clarification_id}",
                details={"clarification_id": str(clarification_id)},
            )
        if row.status == KnowledgeIngestionClarificationStatus.SUPERSEDED.value:
            return row
        if row.status == KnowledgeIngestionClarificationStatus.ANSWERED.value:
            return row  # historical answers remain answered
        return self._repo.update_clarification(
            row, status=KnowledgeIngestionClarificationStatus.SUPERSEDED.value
        )

    def get_clarification(self, clarification_id: uuid.UUID) -> KnowledgeIngestionClarification:
        row = self._repo.get_clarification(clarification_id)
        if row is None:
            raise ValidationFailedError(
                f"Unknown ingestion clarification: {clarification_id}",
                details={"clarification_id": str(clarification_id)},
            )
        return row

    def find_clarification_by_key(
        self, ingestion_id: uuid.UUID, clarification_key: str
    ) -> KnowledgeIngestionClarification | None:
        self.get_ingestion(ingestion_id)
        return self._repo.find_clarification_by_key(ingestion_id, clarification_key)

    def list_clarifications(self, ingestion_id: uuid.UUID) -> list[KnowledgeIngestionClarification]:
        self.get_ingestion(ingestion_id)
        return self._repo.list_clarifications(ingestion_id)

    # --- effects ---

    def record_effect(
        self,
        *,
        ingestion_id: uuid.UUID,
        candidate_id: uuid.UUID,
        effect_key: str,
        effect_type: KnowledgeIngestionEffectType | str,
        status: KnowledgeIngestionEffectStatus | str = KnowledgeIngestionEffectStatus.FAILED,
        operation_id: uuid.UUID | None = None,
        statement_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        details_json: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> KnowledgeIngestionEffect:
        ingestion = self.get_ingestion(ingestion_id)
        if ingestion.mode == KnowledgeIngestionMode.DRY_RUN.value:
            raise InvalidStateTransitionError(
                "Dry-run ingestion may never record persistence effects",
                details={
                    "ingestion_id": str(ingestion_id),
                    "mode": ingestion.mode,
                    "effect_key": effect_key,
                },
            )
        self._require_candidate_on_ingestion(ingestion_id, candidate_id)
        type_value = str(effect_type)
        status_value = str(status)
        if type_value not in {m.value for m in KnowledgeIngestionEffectType}:
            raise ValidationFailedError(
                f"Invalid effect_type: {type_value}",
                details={"effect_type": type_value},
            )
        if status_value not in {m.value for m in KnowledgeIngestionEffectStatus}:
            raise ValidationFailedError(
                f"Invalid effect status: {status_value}",
                details={"status": status_value},
            )
        try:
            with self._session.begin_nested():
                return self._repo.create_effect(
                    ingestion_id=ingestion_id,
                    candidate_id=candidate_id,
                    effect_key=effect_key,
                    effect_type=type_value,
                    status=status_value,
                    operation_id=operation_id,
                    statement_id=statement_id,
                    entity_id=entity_id,
                    details_json=details_json,
                    error_code=error_code,
                    error_message=error_message,
                    idempotency_key=effect_idempotency_key(ingestion_id, candidate_id, effect_key),
                )
        except IntegrityError as exc:
            raise ValidationFailedError(
                "effect_key must be unique within (ingestion, candidate)",
                details={
                    "ingestion_id": str(ingestion_id),
                    "candidate_id": str(candidate_id),
                    "effect_key": effect_key,
                },
            ) from exc

    def get_effect_by_key(
        self,
        ingestion_id: uuid.UUID,
        candidate_id: uuid.UUID,
        effect_key: str,
    ) -> KnowledgeIngestionEffect | None:
        return self._repo.find_effect_by_key(ingestion_id, candidate_id, effect_key)

    def mark_effect_applied(
        self,
        *,
        ingestion_id: uuid.UUID,
        candidate_id: uuid.UUID,
        effect_key: str,
        operation_id: uuid.UUID | None = None,
        statement_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        details_json: dict[str, Any] | None = None,
    ) -> KnowledgeIngestionEffect:
        self.get_ingestion(ingestion_id)
        self._require_candidate_on_ingestion(ingestion_id, candidate_id)
        row = self._repo.find_effect_by_key(ingestion_id, candidate_id, effect_key)
        if row is None:
            raise ValidationFailedError(
                "Unknown effect for mark_applied",
                details={
                    "ingestion_id": str(ingestion_id),
                    "candidate_id": str(candidate_id),
                    "effect_key": effect_key,
                },
            )
        return self._repo.update_effect(
            row,
            status=KnowledgeIngestionEffectStatus.APPLIED.value,
            operation_id=operation_id,
            statement_id=statement_id,
            entity_id=entity_id,
            details_json=details_json,
            error_code=None,
            error_message=None,
        )

    def mark_effect_failed(
        self,
        *,
        ingestion_id: uuid.UUID,
        candidate_id: uuid.UUID,
        effect_key: str,
        error_code: str | None = None,
        error_message: str | None = None,
        details_json: dict[str, Any] | None = None,
    ) -> KnowledgeIngestionEffect:
        self.get_ingestion(ingestion_id)
        self._require_candidate_on_ingestion(ingestion_id, candidate_id)
        row = self._repo.find_effect_by_key(ingestion_id, candidate_id, effect_key)
        if row is None:
            raise ValidationFailedError(
                "Unknown effect for mark_failed",
                details={
                    "ingestion_id": str(ingestion_id),
                    "candidate_id": str(candidate_id),
                    "effect_key": effect_key,
                },
            )
        if row.status == KnowledgeIngestionEffectStatus.APPLIED.value:
            raise InvalidStateTransitionError(
                "Applied effects are historical and cannot become failed",
                details={
                    "ingestion_id": str(ingestion_id),
                    "candidate_id": str(candidate_id),
                    "effect_key": effect_key,
                    "status": row.status,
                },
            )
        return self._repo.update_effect(
            row,
            status=KnowledgeIngestionEffectStatus.FAILED.value,
            error_code=error_code,
            error_message=error_message,
            details_json=details_json,
        )

    def list_effects(self, ingestion_id: uuid.UUID) -> list[KnowledgeIngestionEffect]:
        self.get_ingestion(ingestion_id)
        return self._repo.list_effects(ingestion_id)
