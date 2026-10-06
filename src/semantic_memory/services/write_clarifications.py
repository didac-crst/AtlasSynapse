"""Issue and answer identity/write clarification continuation handles."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.exceptions import (
    ClarificationRequestAlreadyResolvedError,
    ClarificationRequestNotFoundError,
    ClarificationRequestSupersededError,
    ValidationFailedError,
)
from semantic_memory.models import EntityStatus
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import WriteClarificationResolution, WriteClarificationStatus
from semantic_memory.models.operations import WriteClarificationRequest
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.write_clarifications import WriteClarificationRepository
from semantic_memory.schemas.dry_run import OperationMode
from semantic_memory.schemas.identity import IdentityResolutionOutcome, IdentityResolutionResult
from semantic_memory.schemas.statements import (
    AssertionOutcome,
    AssertStatementRequest,
    AssertStatementResponse,
)
from semantic_memory.schemas.write_clarifications import (
    AnswerIdentityClarificationRequest,
    AnswerIdentityClarificationResponse,
    WriteClarificationInfo,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.mutations import MutationRunner


class WriteClarificationService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._actors = ActorService(session)
        self._entities = EntityRepository(session)
        self._clarifications = WriteClarificationRepository(session)
        self._mutations = MutationRunner(session)

    def issue_for_assert(
        self,
        *,
        request: AssertStatementRequest,
        result: AssertStatementResponse,
        actor_id: uuid.UUID,
        supersedes: uuid.UUID | None = None,
    ) -> WriteClarificationRequest:
        """Persist a control-plane handle after CLARIFY (outside dry-run rollback)."""
        ambiguous_path = self._primary_ambiguous_path(result)
        identity = (
            result.subject_identity if ambiguous_path == "subject" else result.object_identity
        )
        candidate_ids = self._candidate_ids(identity)
        operation_mode = OperationMode.DRY_RUN if request.dry_run else OperationMode.EXECUTE
        frozen = request.model_dump(mode="json")
        snapshot = {
            "subject_identity": None
            if result.subject_identity is None
            else result.subject_identity.model_dump(mode="json"),
            "object_identity": None
            if result.object_identity is None
            else result.object_identity.model_dump(mode="json"),
            "question": self._question_for(ambiguous_path, identity),
        }
        return self._clarifications.create(
            actor_id=actor_id,
            operation_name="assert_statement",
            operation_mode=operation_mode.value,
            original_request_id=request.request_id,
            ambiguous_path=ambiguous_path,
            frozen_request=frozen,
            identity_snapshot=snapshot,
            candidate_entity_ids=candidate_ids,
            ttl_minutes=self._settings.write_clarification_ttl_minutes,
            supersedes_clarification_request_id=supersedes,
        )

    def to_info(self, row: WriteClarificationRequest) -> WriteClarificationInfo:
        question = None
        if isinstance(row.identity_snapshot, dict):
            raw = row.identity_snapshot.get("question")
            question = str(raw) if raw else None
        return WriteClarificationInfo(
            clarification_request_id=row.id,
            ambiguous_path=row.ambiguous_path,  # type: ignore[arg-type]
            operation_mode=OperationMode(row.operation_mode),
            status=WriteClarificationStatus(row.status),
            expires_at=row.expires_at,
            candidate_entity_ids=[uuid.UUID(str(item)) for item in row.candidate_entity_ids],
            question=question,
        )

    def answer(
        self, request: AnswerIdentityClarificationRequest
    ) -> AnswerIdentityClarificationResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        # Expire outside the mutation savepoint so TTL transitions survive a rejected answer.
        row = self._clarifications.get(request.clarification_request_id)
        if row is not None:
            self._clarifications.mark_expired_if_needed(row)
            self._session.flush()
        return self._mutations.run(
            actor=actor,
            operation_name="answer_identity_clarification",
            request=request,
            response_model=AnswerIdentityClarificationResponse,
            constraint_name="write_clarification",
            execute=lambda: self._answer_body(request=request, actor_id=actor.id),
        )

    def gc_expired(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self._settings.write_clarification_gc_days)
        return self._clarifications.delete_older_than(older_than=cutoff)

    def _answer_body(
        self, *, request: AnswerIdentityClarificationRequest, actor_id: uuid.UUID
    ) -> AnswerIdentityClarificationResponse:
        # Import lazily to avoid circular imports with StatementService.
        from semantic_memory.services.statements import StatementService

        row = self._clarifications.get(request.clarification_request_id)
        if row is None:
            raise ClarificationRequestNotFoundError(
                "Write clarification request was not found",
                details={"clarification_request_id": str(request.clarification_request_id)},
                request_id=str(request.request_id),
            )
        if row.status == WriteClarificationStatus.SUPERSEDED.value:
            raise ClarificationRequestSupersededError(
                "Write clarification request was superseded",
                details={"clarification_request_id": str(row.id)},
                request_id=str(request.request_id),
            )
        if row.status == WriteClarificationStatus.EXPIRED.value:
            raise ClarificationRequestAlreadyResolvedError(
                "Write clarification request has expired",
                details={
                    "clarification_request_id": str(row.id),
                    "status": row.status,
                    "expires_at": row.expires_at.isoformat(),
                },
                request_id=str(request.request_id),
            )
        if row.status != WriteClarificationStatus.OPEN.value:
            raise ClarificationRequestAlreadyResolvedError(
                "Write clarification request is not open",
                details={
                    "clarification_request_id": str(row.id),
                    "status": row.status,
                },
                request_id=str(request.request_id),
            )

        if request.resolution == WriteClarificationResolution.REJECT:
            self._clarifications.mark_resolved(
                row,
                resolution=request.resolution.value,
                answered_by_actor_id=actor_id,
                resulting_request_id=request.request_id,
            )
            return AnswerIdentityClarificationResponse(
                clarification_request_id=row.id,
                status=WriteClarificationStatus.RESOLVED,
                resolution=WriteClarificationResolution.REJECT,
                assert_result=None,
                request_id=request.request_id,
                message="Write clarification rejected; no knowledge write performed",
            )

        frozen = AssertStatementRequest.model_validate(row.frozen_request)
        # Resume always uses a fresh request_id / idempotency key (nested under answer).
        # A dry-run clarification can never resume into an execute write.
        frozen = frozen.model_copy(
            update={
                "request_id": uuid.uuid4(),
                "idempotency_key": f"{request.idempotency_key}:write-clarification-resume",
                "actor_key": request.actor_key,
                "trace_id": request.trace_id,
                "dry_run": row.operation_mode == OperationMode.DRY_RUN.value,
            }
        )

        force_create_sides: set[str] = set()
        if request.resolution == WriteClarificationResolution.CHOSEN_ENTITY:
            assert request.chosen_entity_id is not None
            stale = self._staleness_for_chosen(
                row=row,
                chosen_entity_id=request.chosen_entity_id,
                request_id=request.request_id,
            )
            if stale is not None:
                return stale
            frozen = self._apply_chosen_entity(
                frozen=frozen,
                path=row.ambiguous_path,  # type: ignore[arg-type]
                entity_id=request.chosen_entity_id,
            )
        elif request.resolution == WriteClarificationResolution.CREATE_NEW:
            force_create_sides.add(row.ambiguous_path)
            # Keep EntityInput on that side; force_create skips ambiguity.
            if row.ambiguous_path == "subject" and frozen.subject is None:
                raise ValidationFailedError(
                    "Frozen request has no subject EntityInput for create_new",
                    details={"clarification_request_id": str(row.id)},
                    request_id=str(request.request_id),
                )
            if row.ambiguous_path == "object" and frozen.object is None:
                raise ValidationFailedError(
                    "Frozen request has no object EntityInput for create_new",
                    details={"clarification_request_id": str(row.id)},
                    request_id=str(request.request_id),
                )

        statements = StatementService(self._session)
        result = statements.assert_statement_resumed(
            request=frozen,
            actor_id=actor_id,
            force_create_sides=force_create_sides,
        )

        if result.outcome == AssertionOutcome.CLARIFY:
            self._clarifications.supersede(row)
            new_row = self.issue_for_assert(
                request=frozen,
                result=result,
                actor_id=actor_id,
                supersedes=row.id,
            )
            result = result.model_copy(update={"clarification_request_id": new_row.id})
            return AnswerIdentityClarificationResponse(
                clarification_request_id=row.id,
                status=WriteClarificationStatus.SUPERSEDED,
                resolution=request.resolution,
                assert_result=result,
                open_clarification=self.to_info(new_row),
                request_id=request.request_id,
                message=(
                    "Situation changed or another side is still ambiguous; "
                    "a fresh clarification_request_id was issued"
                ),
            )

        self._clarifications.mark_resolved(
            row,
            resolution=request.resolution.value,
            answered_by_actor_id=actor_id,
            chosen_entity_id=request.chosen_entity_id,
            resulting_request_id=request.request_id,
        )
        return AnswerIdentityClarificationResponse(
            clarification_request_id=row.id,
            status=WriteClarificationStatus.RESOLVED,
            resolution=request.resolution,
            assert_result=result,
            request_id=request.request_id,
        )

    def _staleness_for_chosen(
        self,
        *,
        row: WriteClarificationRequest,
        chosen_entity_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> AnswerIdentityClarificationResponse | None:
        from semantic_memory.services.statements import StatementService

        candidate_ids = {uuid.UUID(str(item)) for item in row.candidate_entity_ids}
        if chosen_entity_id not in candidate_ids:
            raise ValidationFailedError(
                "chosen_entity_id was not in the clarification candidate set",
                details={
                    "chosen_entity_id": str(chosen_entity_id),
                    "candidate_entity_ids": [str(item) for item in candidate_ids],
                },
                request_id=str(request_id),
            )
        entity = self._entities.get(chosen_entity_id)
        if entity is not None and entity.status == EntityStatus.ACTIVE.value:
            return None

        frozen = AssertStatementRequest.model_validate(row.frozen_request)
        frozen = frozen.model_copy(
            update={
                "request_id": uuid.uuid4(),
                "idempotency_key": f"{request_id}:stale-recheck",
                "dry_run": row.operation_mode == OperationMode.DRY_RUN.value,
            }
        )
        actor = self._actors.require_active_actor(frozen.actor_key)
        result = StatementService(self._session).assert_statement(frozen)
        if result.outcome == AssertionOutcome.CLARIFY:
            self._clarifications.supersede(row)
            new_row = self.issue_for_assert(
                request=frozen,
                result=result,
                actor_id=actor.id,
                supersedes=row.id,
            )
            result = result.model_copy(update={"clarification_request_id": new_row.id})
            return AnswerIdentityClarificationResponse(
                clarification_request_id=row.id,
                status=WriteClarificationStatus.SUPERSEDED,
                resolution=WriteClarificationResolution.CHOSEN_ENTITY,
                assert_result=result,
                open_clarification=self.to_info(new_row),
                request_id=request_id,
                message=(
                    "Chosen entity is no longer active; a fresh clarification "
                    "was issued against current prod state"
                ),
            )
        self._clarifications.mark_resolved(
            row,
            resolution=WriteClarificationResolution.CHOSEN_ENTITY.value,
            answered_by_actor_id=actor.id,
            chosen_entity_id=chosen_entity_id,
            resulting_request_id=request_id,
        )
        return AnswerIdentityClarificationResponse(
            clarification_request_id=row.id,
            status=WriteClarificationStatus.RESOLVED,
            resolution=WriteClarificationResolution.CHOSEN_ENTITY,
            assert_result=result,
            request_id=request_id,
            message="Chosen entity inactive; re-ran frozen operation against current prod",
        )

    def _apply_chosen_entity(
        self,
        *,
        frozen: AssertStatementRequest,
        path: Literal["subject", "object"],
        entity_id: uuid.UUID,
    ) -> AssertStatementRequest:
        if path == "subject":
            return frozen.model_copy(update={"subject_entity_id": entity_id, "subject": None})
        return frozen.model_copy(update={"object_entity_id": entity_id, "object": None})

    @staticmethod
    def _primary_ambiguous_path(
        result: AssertStatementResponse,
    ) -> Literal["subject", "object"]:
        if (
            result.subject_identity is not None
            and result.subject_identity.resolution == IdentityResolutionOutcome.AMBIGUOUS
        ):
            return "subject"
        if (
            result.object_identity is not None
            and result.object_identity.resolution == IdentityResolutionOutcome.AMBIGUOUS
        ):
            return "object"
        raise ValidationFailedError(
            "CLARIFY response has no ambiguous identity side",
            details={},
        )

    @staticmethod
    def _candidate_ids(identity: IdentityResolutionResult | None) -> list[str]:
        if identity is None:
            return []
        return [str(item.entity_id) for item in identity.candidates]

    @staticmethod
    def _question_for(path: str, identity: IdentityResolutionResult | None) -> str:
        names: list[str] = []
        if identity is not None:
            names = [item.canonical_name for item in identity.candidates[:5]]
        if names:
            listed = "; ".join(names)
            return (
                f"Which entity did you mean for {path}? Candidates: {listed}. "
                "Answer via answer_identity_clarification with chosen_entity_id, "
                "create_new, or reject."
            )
        return (
            f"Identity for {path} is ambiguous. Answer via "
            "answer_identity_clarification with chosen_entity_id, create_new, or reject."
        )
