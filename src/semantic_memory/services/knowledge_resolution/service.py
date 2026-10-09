"""Phase D agenda / fixpoint: resolve candidates to eligible or blocked."""

from __future__ import annotations

import uuid
from collections import deque
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models.enums import (
    KnowledgeCandidateState,
    KnowledgeIngestionMode,
    KnowledgeIngestionStatus,
)
from semantic_memory.repositories.llm_calls import LlmCallLogRepository
from semantic_memory.services.actors import ActorService
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService
from semantic_memory.services.knowledge_resolution.budgets import ResolutionBudgets
from semantic_memory.services.knowledge_resolution.resolve_candidate import resolve_candidate
from semantic_memory.services.knowledge_resolution.schemas import (
    BlockerType,
    CandidateResolution,
    CommitPath,
    ResolutionBlocker,
    ResolveIngestionStats,
)

_PARENT_READY = frozenset(
    {
        KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value,
        KnowledgeCandidateState.COMMITTED.value,
    }
)
_TERMINAL_CANDIDATE = frozenset(
    {
        KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value,
        KnowledgeCandidateState.COMMITTED.value,
        KnowledgeCandidateState.DISCARDED.value,
        KnowledgeCandidateState.FAILED.value,
    }
)


class KnowledgeResolutionService:
    """Dependency-aware resolution agenda (no live knowledge commits)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ki = KnowledgeIngestionService(session)
        self._actors = ActorService(session)
        self._llm_calls = LlmCallLogRepository(session)

    def resolve_ingestion(self, ingestion_id: uuid.UUID) -> ResolveIngestionStats:
        ingestion = self._ki.get_ingestion(ingestion_id)
        dry_run = ingestion.mode == KnowledgeIngestionMode.DRY_RUN.value
        actor = self._actors.get(ingestion.actor_id)
        actor_key = actor.key

        if ingestion.status in {
            KnowledgeIngestionStatus.PAUSED.value,
            KnowledgeIngestionStatus.AWAITING_CLARIFICATION.value,
            KnowledgeIngestionStatus.ACCEPTED.value,
            KnowledgeIngestionStatus.EXTRACTING.value,
        }:
            self._ki.set_ingestion_status(ingestion_id, KnowledgeIngestionStatus.RESOLVING)

        budgets = ResolutionBudgets(
            ingestion.budgets_json if isinstance(ingestion.budgets_json, dict) else {}
        )
        stats = ResolveIngestionStats()
        llm_baseline = self._llm_calls.count()

        deps = self._ki.list_dependencies(ingestion_id)
        parents_by_child: dict[uuid.UUID, list[uuid.UUID]] = {}
        children_by_parent: dict[uuid.UUID, list[uuid.UUID]] = {}
        for edge in deps:
            parents_by_child.setdefault(edge.child_candidate_id, []).append(
                edge.parent_candidate_id
            )
            children_by_parent.setdefault(edge.parent_candidate_id, []).append(
                edge.child_candidate_id
            )

        # Promote extracted → actionable when parents ready; requeue blocked for re-eval.
        self._prepare_agenda(ingestion_id, parents_by_child)

        candidates = {
            c.id: c
            for c in sorted(self._ki.list_candidates(ingestion_id), key=lambda r: r.candidate_key)
        }
        agenda: deque[uuid.UUID] = deque(
            c.id for c in candidates.values() if c.state == KnowledgeCandidateState.ACTIONABLE.value
        )

        while agenda and budgets.can_attempt_candidate() and budgets.check_wall():
            candidate_id = agenda.popleft()
            candidate = self._ki.get_candidate(candidate_id)
            if candidate.state != KnowledgeCandidateState.ACTIONABLE.value:
                continue

            stats.candidates_seen += 1
            unmet = self._unmet_parents(candidate_id, parents_by_child)

            result = resolve_candidate(
                self._session,
                candidate,
                actor_key=actor_key,
                dry_run=dry_run,
                unmet_parent_ids=unmet,
                allow_propose=(budgets.can_propose_ontology() and budgets.remaining_llm_ok()),
            )

            # Account for LLM calls from proposal semantic review (llm_call_log).
            llm_now = self._llm_calls.count()
            delta = max(0, llm_now - llm_baseline - budgets.llm_calls)
            if delta:
                budgets.record_llm_call(delta)

            budgets.record_candidate_attempt()
            stats.candidates_attempted += 1
            if result.ontology_proposal_created:
                budgets.record_ontology_proposal()
                stats.ontology_proposals_created += 1
            if result.ontology_proposal_reused:
                stats.ontology_proposals_reused += 1
            stats.identity_blockers += result.identity_blockers

            if result.resolution.commit_path == CommitPath.CLAIM:
                stats.claim_path += 1
            else:
                stats.domain_path += 1

            new_state = (
                KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE
                if result.eligible
                else KnowledgeCandidateState.BLOCKED
            )
            blockers_payload = [b.model_dump(mode="json") for b in result.blockers]
            previous_state = candidate.state
            previous_resolution = candidate.resolution_json
            previous_blockers = candidate.blockers_json

            self._ki.persist_candidate_resolution(
                candidate_id,
                state=new_state,
                resolution=result.resolution,
                blockers=result.blockers,
                attempt_count=candidate.attempt_count + 1,
            )

            if result.eligible:
                stats.eligible += 1
            else:
                stats.blocked += 1

            meaningful = (
                previous_state != new_state.value
                or previous_resolution != result.resolution.model_dump(mode="json")
                or previous_blockers != blockers_payload
            )
            if meaningful and result.eligible:
                woken = self._wake_children(
                    parent_id=candidate_id,
                    children_by_parent=children_by_parent,
                    agenda=agenda,
                )
                stats.dependents_woken += woken

        # Dependency-only stall / cycle: unresolved remain, agenda empty.
        if not budgets.exhausted:
            stalled = self._detect_and_mark_dependency_stall(
                ingestion_id, parents_by_child=parents_by_child
            )
            if stalled:
                stats.dependency_stall = True
                stats.blocked += stalled

        stats.llm_calls = budgets.llm_calls
        stats.wall_ms = budgets.wall_ms()
        stats.budget_exhausted = budgets.exhausted

        # Refresh ingestion row (status may have changed).
        ingestion = self._ki.get_ingestion(ingestion_id)
        merged_stats = dict(ingestion.stats_json) if isinstance(ingestion.stats_json, dict) else {}
        merged_stats["resolution"] = stats.model_dump(mode="json")

        if budgets.exhausted:
            self._ki.mark_paused_budget_exhausted(ingestion_id, stats_json=merged_stats)
        else:
            self._ki.set_ingestion_status(
                ingestion_id,
                KnowledgeIngestionStatus.RESOLVING,
                stats_json=merged_stats,
            )

        return stats

    def _prepare_agenda(
        self,
        ingestion_id: uuid.UUID,
        parents_by_child: dict[uuid.UUID, list[uuid.UUID]],
    ) -> None:
        for candidate in self._ki.list_candidates(ingestion_id):
            if candidate.state == KnowledgeCandidateState.EXTRACTED.value:
                unmet = self._unmet_parents(candidate.id, parents_by_child)
                if not unmet:
                    self._ki.update_candidate_state(
                        candidate.id, KnowledgeCandidateState.ACTIONABLE
                    )
            elif candidate.state == KnowledgeCandidateState.BLOCKED.value:
                self._ki.update_candidate_state(
                    candidate.id,
                    KnowledgeCandidateState.ACTIONABLE,
                    blockers_json=[],
                )

    def _unmet_parents(
        self,
        child_id: uuid.UUID,
        parents_by_child: dict[uuid.UUID, list[uuid.UUID]],
    ) -> list[uuid.UUID]:
        unmet: list[uuid.UUID] = []
        for parent_id in parents_by_child.get(child_id, []):
            parent = self._ki.get_candidate(parent_id)
            if parent.state not in _PARENT_READY:
                unmet.append(parent_id)
        return unmet

    def _wake_children(
        self,
        *,
        parent_id: uuid.UUID,
        children_by_parent: dict[uuid.UUID, list[uuid.UUID]],
        agenda: deque[uuid.UUID],
    ) -> int:
        woken = 0
        for child_id in children_by_parent.get(parent_id, []):
            child = self._ki.get_candidate(child_id)
            if child.state not in {
                KnowledgeCandidateState.BLOCKED.value,
                KnowledgeCandidateState.EXTRACTED.value,
            }:
                continue
            self._ki.update_candidate_state(
                child_id,
                KnowledgeCandidateState.ACTIONABLE,
                blockers_json=[],
            )
            if child_id not in agenda:
                agenda.append(child_id)
            woken += 1
        return woken

    def _detect_and_mark_dependency_stall(
        self,
        ingestion_id: uuid.UUID,
        *,
        parents_by_child: dict[uuid.UUID, list[uuid.UUID]],
    ) -> int:
        """Mark dependency-only deadlocks/cycles with an explicit typed blocker.

        Returns the number of candidates newly marked blocked for the stall.
        """
        candidates = self._ki.list_candidates(ingestion_id)
        if any(c.state == KnowledgeCandidateState.ACTIONABLE.value for c in candidates):
            return 0

        unresolved = [c for c in candidates if c.state not in _TERMINAL_CANDIDATE]
        if not unresolved:
            return 0

        # Stall only when every unresolved candidate still has unmet parents
        # (cycle or dependency-only deadlock) — not when blocked on ontology/identity.
        stalled_ids: list[uuid.UUID] = []
        for candidate in unresolved:
            unmet = self._unmet_parents(candidate.id, parents_by_child)
            if not unmet and candidate.state == KnowledgeCandidateState.BLOCKED.value:
                # External blockers (identity/ontology/policy) — not a dependency stall.
                continue
            if unmet or candidate.state == KnowledgeCandidateState.EXTRACTED.value:
                stalled_ids.append(candidate.id)

        if not stalled_ids:
            return 0

        marked = 0
        for candidate_id in stalled_ids:
            candidate = self._ki.get_candidate(candidate_id)
            unmet = self._unmet_parents(candidate_id, parents_by_child)
            cycle_refs = [str(pid) for pid in unmet] or [
                str(pid) for pid in parents_by_child.get(candidate_id, [])
            ]
            blockers = [
                ResolutionBlocker(
                    type=BlockerType.DEPENDENCY,
                    field=None,
                    ref=",".join(cycle_refs) if cycle_refs else None,
                    detail="dependency_cycle_or_stall",
                    required=True,
                    parent_candidate_id=unmet[0] if unmet else None,
                )
            ]
            resolution = CandidateResolution(
                commit_path=CommitPath.CLAIM,
                claim_text=candidate.claim_text,
                warnings=[],
            )
            if isinstance(candidate.resolution_json, dict) and candidate.resolution_json:
                try:
                    resolution = CandidateResolution.model_validate(candidate.resolution_json)
                except Exception:  # noqa: BLE001
                    pass
            self._ki.persist_candidate_resolution(
                candidate_id,
                state=KnowledgeCandidateState.BLOCKED,
                resolution=resolution,
                blockers=blockers,
                attempt_count=candidate.attempt_count,
            )
            marked += 1
        return marked


def blockers_from_json(raw: Any) -> list[ResolutionBlocker]:
    if not isinstance(raw, list):
        return []
    out: list[ResolutionBlocker] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(ResolutionBlocker.model_validate(item))
    return out


def only_external_blockers(blockers: list[ResolutionBlocker]) -> bool:
    """True when every blocker needs clarification/policy (agenda can stop)."""
    if not blockers:
        return False
    return all(
        b.type
        in {BlockerType.IDENTITY_CLARIFICATION, BlockerType.POLICY, BlockerType.ONTOLOGY_PROPOSAL}
        for b in blockers
    )
