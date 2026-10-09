"""Phase E: clarification orchestration and continue_ingestion."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.exceptions import InvalidStateTransitionError
from semantic_memory.models.enums import (
    KnowledgeCandidateState,
    KnowledgeIngestionMode,
    KnowledgeIngestionStatus,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.knowledge_continuation.answers import (
    apply_clarification_answers,
    reconcile_ontology_package_clarifications,
)
from semantic_memory.services.knowledge_continuation.overrides import load_resolution_overrides
from semantic_memory.services.knowledge_continuation.planner import ClarificationPlanner
from semantic_memory.services.knowledge_continuation.schemas import (
    ClarificationAnswerInput,
    ContinuationResult,
    ExternalBlockerView,
    OpenClarificationView,
)
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService
from semantic_memory.services.knowledge_resolution import KnowledgeResolutionService

_TERMINAL_RUN = frozenset(
    {
        KnowledgeIngestionStatus.COMPLETED.value,
        KnowledgeIngestionStatus.FAILED.value,
        KnowledgeIngestionStatus.COMMITTING.value,
    }
)


class KnowledgeContinuationService:
    """Internal continuation + clarification planner (no HTTP/MCP)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ki = KnowledgeIngestionService(session)
        self._actors = ActorService(session)
        self._resolver = KnowledgeResolutionService(session)
        self._planner = ClarificationPlanner(session)

    def plan_clarifications(self, ingestion_id: uuid.UUID) -> ContinuationResult:
        """Plan clarifications from current blockers without answering or resolving."""
        self._ki.get_ingestion(ingestion_id)
        plan = self._planner.plan(ingestion_id)
        return self._build_result(
            ingestion_id,
            open_clarifications=plan.open_clarifications,
            external=plan.unresolved_external_blockers,
            resolver_stats={},
        )

    def continue_ingestion(
        self,
        ingestion_id: uuid.UUID,
        answers: list[ClarificationAnswerInput] | None = None,
    ) -> ContinuationResult:
        ingestion = self._ki.get_ingestion(ingestion_id)
        status = ingestion.status
        answer_list = list(answers or [])

        if status in _TERMINAL_RUN:
            raise InvalidStateTransitionError(
                "Cannot continue a completed, committing, or failed ingestion",
                details={"ingestion_id": str(ingestion_id), "status": status},
            )

        # Reconcile crash/retry: ontology handle already terminal, package still open.
        reconciled = reconcile_ontology_package_clarifications(
            self._session, ingestion_id=ingestion_id
        )

        # awaiting_clarification + no answers → return open questions without resolver
        # churn, unless ontology reconciliation made progress.
        if (
            status == KnowledgeIngestionStatus.AWAITING_CLARIFICATION.value
            and not answer_list
            and not reconciled
        ):
            plan = self._planner.plan(ingestion_id)
            return self._build_result(
                ingestion_id,
                open_clarifications=plan.open_clarifications,
                external=plan.unresolved_external_blockers,
                resolver_stats={},
            )

        actor = self._actors.get(ingestion.actor_id)

        if answer_list:
            if status not in {
                KnowledgeIngestionStatus.AWAITING_CLARIFICATION.value,
                KnowledgeIngestionStatus.RESOLVING.value,
                KnowledgeIngestionStatus.PAUSED.value,
            }:
                raise InvalidStateTransitionError(
                    "Answers require awaiting_clarification, resolving, or paused status",
                    details={"ingestion_id": str(ingestion_id), "status": status},
                )
            apply_clarification_answers(
                self._session,
                ingestion_id=ingestion_id,
                answers=answer_list,
                actor_key=actor.key,
            )

        # paused/budget_exhausted + no answers: clear pause and take a fresh resolver slice.
        if status == KnowledgeIngestionStatus.PAUSED.value:
            self._ki.set_ingestion_status(ingestion_id, KnowledgeIngestionStatus.RESOLVING)

        # resolving / after answers / paused resume / ontology reconcile → re-run Phase D.
        overrides = load_resolution_overrides(self._session, ingestion_id)
        resolve_stats = self._resolver.resolve_ingestion(ingestion_id, overrides=overrides)

        plan = self._planner.plan(ingestion_id)
        return self._finalize_status(
            ingestion_id,
            open_clarifications=plan.open_clarifications,
            external=plan.unresolved_external_blockers,
            resolver_stats=resolve_stats.model_dump(mode="json"),
        )

    def _finalize_status(
        self,
        ingestion_id: uuid.UUID,
        *,
        open_clarifications: list[OpenClarificationView],
        external: list[ExternalBlockerView],
        resolver_stats: dict[str, Any],
    ) -> ContinuationResult:
        ingestion = self._ki.get_ingestion(ingestion_id)

        if ingestion.status == KnowledgeIngestionStatus.PAUSED.value:
            return self._build_result(
                ingestion_id,
                open_clarifications=open_clarifications,
                external=external,
                resolver_stats=resolver_stats,
            )

        if open_clarifications:
            self._ki.set_ingestion_status(
                ingestion_id,
                KnowledgeIngestionStatus.AWAITING_CLARIFICATION,
                stats_json=_merge_stats(ingestion.stats_json, resolver_stats),
            )
        else:
            # Keep resolving when only external blockers remain, or when ready for commit.
            self._ki.set_ingestion_status(
                ingestion_id,
                KnowledgeIngestionStatus.RESOLVING,
                stats_json=_merge_stats(ingestion.stats_json, resolver_stats),
            )

        return self._build_result(
            ingestion_id,
            open_clarifications=open_clarifications,
            external=external,
            resolver_stats=resolver_stats,
        )

    def _build_result(
        self,
        ingestion_id: uuid.UUID,
        *,
        open_clarifications: list[OpenClarificationView],
        external: list[ExternalBlockerView],
        resolver_stats: dict[str, Any],
    ) -> ContinuationResult:
        ingestion = self._ki.get_ingestion(ingestion_id)
        candidates = self._ki.list_candidates(ingestion_id)
        eligible = sum(
            1
            for c in candidates
            if c.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
        )
        blocked = sum(1 for c in candidates if c.state == KnowledgeCandidateState.BLOCKED.value)
        discarded = sum(1 for c in candidates if c.state == KnowledgeCandidateState.DISCARDED.value)
        non_discarded = [
            c for c in candidates if c.state != KnowledgeCandidateState.DISCARDED.value
        ]
        all_eligible = bool(non_discarded) and all(
            c.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value for c in non_discarded
        )
        dry_run = ingestion.mode == KnowledgeIngestionMode.DRY_RUN.value
        paused = ingestion.status == KnowledgeIngestionStatus.PAUSED.value
        ready = (
            all_eligible and not open_clarifications and not external and not paused and not dry_run
        )
        would_complete = (
            all_eligible and not open_clarifications and not external and not paused and dry_run
        )
        cumulative = (
            dict(ingestion.stats_json.get("resolution_cumulative", {}))
            if isinstance(ingestion.stats_json, dict)
            else {}
        )
        return ContinuationResult(
            ingestion_id=ingestion_id,
            status=ingestion.status,
            ready_for_commit=ready,
            would_complete=would_complete,
            open_clarifications=open_clarifications,
            unresolved_external_blockers=external,
            eligible_count=eligible,
            blocked_count=blocked,
            discarded_count=discarded,
            paused=paused,
            pause_reason=ingestion.pause_reason,
            resolver_stats=resolver_stats,
            cumulative_stats=cumulative,
        )


def _merge_stats(existing: dict[str, Any] | None, resolver_stats: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    if resolver_stats:
        merged["resolution"] = resolver_stats
    return merged
