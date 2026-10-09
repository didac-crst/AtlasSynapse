"""Plan ranked durable package clarifications from Phase D blockers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models.enums import (
    KnowledgeCandidateState,
    KnowledgeIngestionClarificationKind,
    KnowledgeIngestionClarificationStatus,
)
from semantic_memory.models.knowledge_ingestion import KnowledgeCandidate
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.schemas.identity import IdentityResolutionOutcome
from semantic_memory.services.identity import IdentityService
from semantic_memory.services.knowledge_continuation.impact import (
    build_children_map,
    impact_blocked_count,
)
from semantic_memory.services.knowledge_continuation.keys import (
    IdentitySnapshotEntry,
    identity_clarification_key,
    ontology_clarification_key,
)
from semantic_memory.services.knowledge_continuation.schemas import (
    ExternalBlockerKind,
    ExternalBlockerView,
    OpenClarificationView,
)
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService
from semantic_memory.services.knowledge_resolution.schemas import (
    BlockerType,
    CandidateResolution,
    ProjectionDisposition,
    ResolutionBlocker,
)
from semantic_memory.services.knowledge_resolution.service import blockers_from_json


@dataclass
class PlannedClarification:
    clarification_key: str
    clarification_kind: KnowledgeIngestionClarificationKind
    root_candidate_id: uuid.UUID | None
    impact_roots: set[uuid.UUID]
    question_payload: dict[str, Any]
    ontology_clarification_request_id: uuid.UUID | None = None
    ontology_proposal_id: uuid.UUID | None = None


@dataclass
class PlanClarificationsResult:
    open_clarifications: list[OpenClarificationView] = field(default_factory=list)
    unresolved_external_blockers: list[ExternalBlockerView] = field(default_factory=list)


class ClarificationPlanner:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._ki = KnowledgeIngestionService(session)
        self._entities = EntityRepository(session)

    def plan(self, ingestion_id: uuid.UUID) -> PlanClarificationsResult:
        candidates = self._ki.list_candidates(ingestion_id)
        candidates_by_id = {c.id: c for c in candidates}
        deps = self._ki.list_dependencies(ingestion_id)
        children_by_parent = build_children_map(deps)

        planned: dict[str, PlannedClarification] = {}
        external: list[ExternalBlockerView] = []

        for candidate in candidates:
            if candidate.state != KnowledgeCandidateState.BLOCKED.value:
                continue
            blockers = blockers_from_json(candidate.blockers_json)
            resolution = _safe_resolution(candidate)
            for blocker in blockers:
                item = self._classify_blocker(
                    candidate=candidate,
                    blocker=blocker,
                    resolution=resolution,
                )
                if item is None:
                    continue
                if isinstance(item, ExternalBlockerView):
                    external.append(item)
                    continue
                existing = planned.get(item.clarification_key)
                if existing is None:
                    planned[item.clarification_key] = item
                else:
                    existing.impact_roots |= item.impact_roots

        # Persist / reuse rows. Link+supersede prior open rows for the same
        # root+field when the fingerprint (clarification_key) changed, then
        # mop up any remaining stale open identity keys.
        active_keys = set(planned.keys())

        open_views: list[OpenClarificationView] = []
        for key in sorted(
            planned.keys(),
            key=lambda k: (
                -impact_blocked_count(
                    planned[k].impact_roots,
                    children_by_parent=children_by_parent,
                    candidates_by_id=candidates_by_id,
                ),
                k,
            ),
        ):
            plan = planned[key]
            impact = impact_blocked_count(
                plan.impact_roots,
                children_by_parent=children_by_parent,
                candidates_by_id=candidates_by_id,
            )
            prior = self._find_prior_for_lineage(ingestion_id, plan=plan, active_keys=active_keys)
            supersedes_id = prior.id if prior is not None else None
            if (
                prior is not None
                and prior.status == KnowledgeIngestionClarificationStatus.OPEN.value
            ):
                # Open priors are superseded; answered priors keep their status/history.
                self._ki.supersede_clarification(prior.id)

            row = self._ki.record_clarification(
                ingestion_id=ingestion_id,
                clarification_key=plan.clarification_key,
                clarification_kind=plan.clarification_kind,
                question_payload=plan.question_payload,
                root_candidate_id=plan.root_candidate_id,
                impact_blocked_count=impact,
                ontology_clarification_request_id=plan.ontology_clarification_request_id,
                supersedes_clarification_id=supersedes_id,
            )
            if row.status != KnowledgeIngestionClarificationStatus.OPEN.value:
                continue
            open_views.append(
                OpenClarificationView(
                    clarification_id=row.id,
                    clarification_key=row.clarification_key,
                    clarification_kind=row.clarification_kind,
                    impact_blocked_count=row.impact_blocked_count,
                    root_candidate_id=row.root_candidate_id,
                    question_payload=row.question_payload
                    if isinstance(row.question_payload, dict)
                    else {},
                    ontology_clarification_request_id=row.ontology_clarification_request_id,
                    supersedes_clarification_id=row.supersedes_clarification_id,
                )
            )

        self._supersede_stale_open(ingestion_id, active_keys=active_keys)

        open_views.sort(
            key=lambda v: (-v.impact_blocked_count, v.clarification_key),
        )
        return PlanClarificationsResult(
            open_clarifications=open_views,
            unresolved_external_blockers=external,
        )

    def _classify_blocker(
        self,
        *,
        candidate: KnowledgeCandidate,
        blocker: ResolutionBlocker,
        resolution: CandidateResolution | None,
    ) -> PlannedClarification | ExternalBlockerView | None:
        if blocker.type == BlockerType.IDENTITY_CLARIFICATION:
            return self._plan_identity(candidate, blocker, resolution)

        if blocker.type == BlockerType.ONTOLOGY_PROPOSAL:
            if blocker.ontology_clarification_request_id is not None:
                key = ontology_clarification_key(
                    ontology_clarification_request_id=blocker.ontology_clarification_request_id
                )
                return PlannedClarification(
                    clarification_key=key,
                    clarification_kind=KnowledgeIngestionClarificationKind.ONTOLOGY,
                    root_candidate_id=candidate.id,
                    impact_roots={candidate.id},
                    question_payload={
                        "candidate_id": str(candidate.id),
                        "ontology_clarification_request_id": str(
                            blocker.ontology_clarification_request_id
                        ),
                        "ontology_proposal_id": (
                            None
                            if blocker.ontology_proposal_id is None
                            else str(blocker.ontology_proposal_id)
                        ),
                        "detail": blocker.detail,
                    },
                    ontology_clarification_request_id=blocker.ontology_clarification_request_id,
                    ontology_proposal_id=blocker.ontology_proposal_id,
                )
            # READY_TO_APPLY / would_propose / pending apply — not a user question.
            return ExternalBlockerView(
                kind=ExternalBlockerKind.ONTOLOGY_APPLY,
                detail=blocker.detail,
                candidate_id=candidate.id,
                ontology_proposal_id=blocker.ontology_proposal_id,
                ref=blocker.ref,
            )

        if blocker.type == BlockerType.DEPENDENCY:
            return ExternalBlockerView(
                kind=ExternalBlockerKind.DEPENDENCY_STALL,
                detail=blocker.detail,
                candidate_id=candidate.id,
                ref=blocker.ref,
            )

        if blocker.type == BlockerType.POLICY:
            # Only surface when structured choices exist in detail payload — none in V1.
            return ExternalBlockerView(
                kind=ExternalBlockerKind.POLICY,
                detail=blocker.detail,
                candidate_id=candidate.id,
                ontology_proposal_id=blocker.ontology_proposal_id,
                ref=blocker.ref,
            )
        return None

    def _plan_identity(
        self,
        candidate: KnowledgeCandidate,
        blocker: ResolutionBlocker,
        resolution: CandidateResolution | None,
    ) -> PlannedClarification | None:
        field = blocker.field or "subject"
        if field not in {"subject", "object"}:
            return None
        bind = None
        if resolution is not None:
            bind = resolution.subject if field == "subject" else resolution.object
        if bind is None or bind.disposition != ProjectionDisposition.CLARIFY:
            # Still plan from blocker if we have candidate IDs on bind; else skip inventing.
            if bind is None or not bind.candidate_entity_ids:
                return None
        entity_ids = list(bind.candidate_entity_ids) if bind is not None else []
        incoming = bind.text if bind is not None else blocker.ref
        if not entity_ids:
            return None

        snapshot = self._identity_snapshot(entity_ids)
        key = identity_clarification_key(
            candidate_id=candidate.id,
            field=field,
            snapshot=snapshot,
        )
        payload = self._identity_question_payload(
            candidate_id=candidate.id,
            field=field,
            incoming_name=incoming,
            entity_ids=entity_ids,
            snapshot=snapshot,
        )
        return PlannedClarification(
            clarification_key=key,
            clarification_kind=KnowledgeIngestionClarificationKind.IDENTITY,
            root_candidate_id=candidate.id,
            impact_roots={candidate.id},
            question_payload=payload,
        )

    def _identity_snapshot(self, entity_ids: list[uuid.UUID]) -> list[IdentitySnapshotEntry]:
        """Build canonical resolution-relevant snapshot for fingerprinting."""
        out: list[IdentitySnapshotEntry] = []
        for eid in entity_ids:
            entity = self._entities.get(eid)
            if entity is None:
                out.append(IdentitySnapshotEntry(entity_id=eid, status="missing", class_keys=()))
                continue
            types = self._entities.list_types(eid)
            out.append(
                IdentitySnapshotEntry(
                    entity_id=eid,
                    status=str(entity.status),
                    class_keys=tuple(sorted(cls.key for cls, _ns in types)),
                )
            )
        return out

    def _identity_question_payload(
        self,
        *,
        candidate_id: uuid.UUID,
        field: str,
        incoming_name: str | None,
        entity_ids: list[uuid.UUID],
        snapshot: list[IdentitySnapshotEntry],
    ) -> dict[str, Any]:
        # Re-probe IdentityService for structured evidence (authority), filtered to offered IDs.
        evidence_by_id: dict[uuid.UUID, dict[str, Any]] = {}
        if incoming_name:
            result = IdentityService(self._session).resolve(
                canonical_name=incoming_name, adjudicate=False
            )
            if (
                result.identity is not None
                and result.identity.resolution == IdentityResolutionOutcome.AMBIGUOUS
            ):
                for cand in result.identity.candidates:
                    if cand.entity_id in set(entity_ids):
                        evidence_by_id[cand.entity_id] = {
                            "entity_id": str(cand.entity_id),
                            "canonical_name": cand.canonical_name,
                            "class_keys": list(cand.class_keys),
                            "decision": cand.decision.value,
                            "reasons": [r.model_dump(mode="json") for r in cand.reasons],
                        }

        status_by_id = {e.entity_id: e.status for e in snapshot}
        class_by_id = {e.entity_id: list(e.class_keys) for e in snapshot}

        candidate_entities: list[dict[str, Any]] = []
        for eid in sorted(entity_ids, key=str):
            base = evidence_by_id.get(eid)
            if base is not None:
                candidate_entities.append(
                    {
                        **base,
                        "status": status_by_id.get(eid, "missing"),
                        "class_keys": class_by_id.get(eid, list(base.get("class_keys") or [])),
                    }
                )
                continue
            entity = self._entities.get(eid)
            if entity is None:
                candidate_entities.append(
                    {
                        "entity_id": str(eid),
                        "canonical_name": None,
                        "class_keys": [],
                        "status": "missing",
                        "reasons": [],
                    }
                )
                continue
            candidate_entities.append(
                {
                    "entity_id": str(eid),
                    "canonical_name": entity.canonical_name,
                    "class_keys": class_by_id.get(eid, []),
                    "status": status_by_id.get(eid, entity.status),
                    "reasons": [],
                }
            )

        return {
            "candidate_id": str(candidate_id),
            "field": field,
            "incoming_name": incoming_name,
            "candidate_entities": candidate_entities,
            "allowed_resolutions": ["chosen_entity", "create_new", "reject"],
        }

    def _supersede_stale_open(self, ingestion_id: uuid.UUID, *, active_keys: set[str]) -> None:
        """Supersede open identity clarifications whose key is no longer active."""
        for row in self._ki.list_clarifications(ingestion_id):
            if row.status != KnowledgeIngestionClarificationStatus.OPEN.value:
                continue
            if row.clarification_kind != KnowledgeIngestionClarificationKind.IDENTITY.value:
                continue
            if row.clarification_key not in active_keys:
                self._ki.supersede_clarification(row.id)

    def _find_prior_for_lineage(
        self,
        ingestion_id: uuid.UUID,
        *,
        plan: PlannedClarification,
        active_keys: set[str],
    ) -> Any:
        """Find prior open/answered identity clarification for same root+field, different key.

        Answered priors remain answered; callers only mark open priors superseded.
        Prefer the newest matching prior (open first, then answered).
        """
        if plan.clarification_kind != KnowledgeIngestionClarificationKind.IDENTITY:
            return None
        field = plan.question_payload.get("field")
        root = plan.root_candidate_id
        open_match = None
        answered_match = None
        for row in self._ki.list_clarifications(ingestion_id):
            if row.clarification_kind != KnowledgeIngestionClarificationKind.IDENTITY.value:
                continue
            if row.clarification_key == plan.clarification_key:
                continue
            if row.clarification_key in active_keys:
                continue
            if row.root_candidate_id != root:
                continue
            payload = row.question_payload if isinstance(row.question_payload, dict) else {}
            if payload.get("field") != field:
                continue
            if row.status == KnowledgeIngestionClarificationStatus.OPEN.value:
                open_match = row
            elif row.status == KnowledgeIngestionClarificationStatus.ANSWERED.value:
                answered_match = row
        return open_match if open_match is not None else answered_match


def _safe_resolution(candidate: KnowledgeCandidate) -> CandidateResolution | None:
    raw = candidate.resolution_json
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return CandidateResolution.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None
