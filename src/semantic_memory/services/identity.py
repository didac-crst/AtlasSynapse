"""Deterministic entity identity resolution (two-level adjudication)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from semantic_memory.models import Entity, OntologyClass
from semantic_memory.models.enums import AliasIdentityStrength, EntityStatus
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.schemas.entities import EntityCandidate, ResolutionOutcome
from semantic_memory.schemas.identity import (
    CandidateDecision,
    EvidenceStrength,
    IdentityCandidate,
    IdentityEvidence,
    IdentityResolutionOutcome,
    IdentityResolutionResult,
    action_for_resolution,
    aggregate_candidate_decisions,
    to_legacy_resolution_outcome,
)
from semantic_memory.schemas.identity_adjudication import (
    IdentityAdjudicationRequest,
    IdentityAdjudicationResult,
)
from semantic_memory.services.identity_adjudication import (
    IdentityAdjudicationService,
    apply_adjudication_to_candidates,
    llm_adjudication_may_run,
)
from semantic_memory.services.identity_graph_evidence import (
    IdentityGraphEvidenceService,
    sort_candidates_for_explanation,
)


@dataclass
class ResolutionResult:
    """Legacy-compatible create_entity surface plus full identity payload."""

    outcome: ResolutionOutcome
    entity: Entity | None = None
    candidates: list[EntityCandidate] = field(default_factory=list)
    match_reason: str | None = None
    identity: IdentityResolutionResult | None = None


@dataclass
class _CandidateAccum:
    entity: Entity
    reasons: list[IdentityEvidence] = field(default_factory=list)
    decisive_same: bool = False
    supporting_hit: bool = False


class IdentityService:
    """Resolve entity identity without silent merges or false collapses."""

    def __init__(
        self,
        session: Session,
        *,
        adjudication: IdentityAdjudicationService | None = None,
    ) -> None:
        self._session = session
        self._entities = EntityRepository(session)
        self._graph_evidence = IdentityGraphEvidenceService(session)
        self._adjudication = adjudication or IdentityAdjudicationService()

    def resolve(
        self,
        *,
        canonical_name: str,
        class_id: uuid.UUID | None = None,
        external_source_system: str | None = None,
        external_id: str | None = None,
        adjudicate: bool = True,
    ) -> ResolutionResult:
        identity_candidates, entity_by_id, class_key = self._gather_candidates(
            canonical_name=canonical_name,
            class_id=class_id,
            external_source_system=external_source_system,
            external_id=external_id,
        )
        adjudication_result: IdentityAdjudicationResult | None = None
        if adjudicate:
            identity_candidates, adjudication_result = self._run_adjudication(
                canonical_name=canonical_name,
                class_key=class_key,
                candidates=identity_candidates,
            )
        return self._to_resolution_result(
            canonical_name=canonical_name,
            identity_candidates=identity_candidates,
            entity_by_id=entity_by_id,
            adjudication_result=adjudication_result,
        )

    def resolve_for_create(
        self,
        *,
        canonical_name: str,
        class_id: uuid.UUID,
        external_source_system: str | None = None,
        external_id: str | None = None,
        acquire_lock: Callable[[], None],
    ) -> ResolutionResult:
        """Create-path resolve: provider HTTP before advisory lock, write under lock.

        ``pg_advisory_xact_lock`` is held until commit, so synchronous adjudication
        must not run after ``acquire_lock`` or concurrent creates stall for tens of
        seconds on shadow/external provider calls.
        """
        pre_candidates, _pre_entities, class_key = self._gather_candidates(
            canonical_name=canonical_name,
            class_id=class_id,
            external_source_system=external_source_system,
            external_id=external_id,
        )
        _pre_candidates, adjudication_result = self._run_adjudication(
            canonical_name=canonical_name,
            class_key=class_key,
            candidates=pre_candidates,
        )

        acquire_lock()
        identity_candidates, entity_by_id, _ = self._gather_candidates(
            canonical_name=canonical_name,
            class_id=class_id,
            external_source_system=external_source_system,
            external_id=external_id,
        )
        if adjudication_result is not None and adjudication_result.enforced:
            identity_candidates = apply_adjudication_to_candidates(
                identity_candidates, adjudication_result
            )
        return self._to_resolution_result(
            canonical_name=canonical_name,
            identity_candidates=identity_candidates,
            entity_by_id=entity_by_id,
            adjudication_result=adjudication_result,
        )

    def _run_adjudication(
        self,
        *,
        canonical_name: str,
        class_key: str | None,
        candidates: list[IdentityCandidate],
    ) -> tuple[list[IdentityCandidate], IdentityAdjudicationResult | None]:
        preliminary = aggregate_candidate_decisions(candidates)
        if not llm_adjudication_may_run(resolution=preliminary, candidates=candidates):
            return candidates, None
        return self._adjudication.maybe_adjudicate(
            request=IdentityAdjudicationRequest(
                incoming_canonical_name=canonical_name,
                class_key=class_key,
                candidates=candidates,
            ),
            resolution=preliminary,
            candidates=candidates,
        )

    def _gather_candidates(
        self,
        *,
        canonical_name: str,
        class_id: uuid.UUID | None = None,
        external_source_system: str | None = None,
        external_id: str | None = None,
    ) -> tuple[list[IdentityCandidate], dict[uuid.UUID, Entity], str | None]:
        by_id: dict[uuid.UUID, _CandidateAccum] = {}

        def _accum(entity: Entity) -> _CandidateAccum:
            row = by_id.get(entity.id)
            if row is None:
                row = _CandidateAccum(entity=entity)
                by_id[entity.id] = row
            return row

        # 1. Authoritative external identity (decisive SAME when exact).
        if external_source_system and external_id:
            matched = self._entities.find_by_external_reference(
                source_system=external_source_system,
                external_id=external_id,
            )
            if matched is not None:
                acc = _accum(matched)
                acc.decisive_same = True
                acc.reasons.append(
                    IdentityEvidence(
                        signal="external_reference_match",
                        strength=EvidenceStrength.DECISIVE,
                        namespace=external_source_system,
                        value=external_id,
                        evidence_refs=[
                            f"entity:{matched.id}",
                            f"external:{external_source_system}:{external_id}",
                        ],
                    )
                )

        # 2. Exact canonical-name match — supporting only (never alone MATCH).
        for entity in self._entities.find_by_canonical_name(canonical_name, class_id=class_id):
            acc = _accum(entity)
            acc.supporting_hit = True
            acc.reasons.append(
                IdentityEvidence(
                    signal="canonical_name_match",
                    strength=EvidenceStrength.SUPPORTING,
                    value=canonical_name,
                    evidence_refs=[f"entity:{entity.id}"],
                )
            )

        # 3. Exact alias matches — strength depends on identity_strength.
        for entity, alias_row in self._entities.find_alias_matches(
            canonical_name, class_id=class_id
        ):
            acc = _accum(entity)
            is_authoritative = (
                alias_row.identity_strength == AliasIdentityStrength.AUTHORITATIVE.value
            )
            if is_authoritative:
                acc.decisive_same = True
                strength = EvidenceStrength.DECISIVE
            else:
                acc.supporting_hit = True
                strength = EvidenceStrength.SUPPORTING
            acc.reasons.append(
                IdentityEvidence(
                    signal="alias_match",
                    strength=strength,
                    value=alias_row.alias,
                    detail=f"identity_strength={alias_row.identity_strength}",
                    evidence_refs=[
                        f"entity:{entity.id}",
                        f"alias:{alias_row.id}",
                    ],
                )
            )

        # 4. Near-name token-subset — supporting / UNCERTAIN only.
        for entity in self._entities.find_near_name_candidates(canonical_name, class_id=class_id):
            acc = _accum(entity)
            # Skip if we already recorded a stronger exact signal for this entity.
            if any(
                r.signal in {"canonical_name_match", "alias_match", "external_reference_match"}
                for r in acc.reasons
            ):
                continue
            acc.supporting_hit = True
            acc.reasons.append(
                IdentityEvidence(
                    signal="near_name",
                    strength=EvidenceStrength.SUPPORTING,
                    value=canonical_name,
                    evidence_refs=[f"entity:{entity.id}"],
                )
            )

        if by_id:
            graph_extra = self._graph_evidence.enrich_candidate_reasons(
                candidate_entity_ids=list(by_id.keys()),
            )
            for entity_id, extra_reasons in graph_extra.items():
                by_id[entity_id].reasons.extend(extra_reasons)

        identity_candidates: list[IdentityCandidate] = []
        entity_by_id: dict[uuid.UUID, Entity] = {}
        for entity_id, acc in by_id.items():
            entity_by_id[entity_id] = acc.entity
            if acc.decisive_same:
                decision = CandidateDecision.SAME
            elif acc.supporting_hit:
                decision = CandidateDecision.UNCERTAIN
            else:
                decision = CandidateDecision.DIFFERENT
            types = self._entities.list_types(entity_id)
            identity_candidates.append(
                IdentityCandidate(
                    entity_id=entity_id,
                    canonical_name=acc.entity.canonical_name,
                    status=EntityStatus(acc.entity.status),
                    class_keys=[ontology_class.key for ontology_class, _ns in types],
                    decision=decision,
                    reasons=list(acc.reasons),
                )
            )

        identity_candidates = sort_candidates_for_explanation(identity_candidates)
        class_key: str | None = None
        if class_id is not None:
            ontology_class = self._session.get(OntologyClass, class_id)
            if ontology_class is not None:
                class_key = ontology_class.key
        return identity_candidates, entity_by_id, class_key

    def _to_resolution_result(
        self,
        *,
        canonical_name: str,
        identity_candidates: list[IdentityCandidate],
        entity_by_id: dict[uuid.UUID, Entity],
        adjudication_result: IdentityAdjudicationResult | None,
    ) -> ResolutionResult:
        resolution = aggregate_candidate_decisions(identity_candidates)
        action = action_for_resolution(resolution)
        matched_entity: Entity | None = None
        decision_basis: str | None = None
        top_reasons: list[IdentityEvidence] = []

        if resolution == IdentityResolutionOutcome.MATCH:
            same = [c for c in identity_candidates if c.decision == CandidateDecision.SAME]
            assert len(same) == 1
            matched_entity = entity_by_id[same[0].entity_id]
            top_reasons = list(same[0].reasons)
            decisive = next(
                (r for r in top_reasons if r.strength == EvidenceStrength.DECISIVE),
                top_reasons[0] if top_reasons else None,
            )
            decision_basis = None if decisive is None else decisive.signal
        elif resolution == IdentityResolutionOutcome.AMBIGUOUS:
            top_reasons = [
                IdentityEvidence(
                    signal="identity_conflict"
                    if _multiple_same(identity_candidates)
                    else "uncertain_identity",
                    strength=EvidenceStrength.DECISIVE
                    if _multiple_same(identity_candidates)
                    else EvidenceStrength.SUPPORTING,
                    value=canonical_name,
                    detail=_ambiguous_detail(identity_candidates),
                    evidence_refs=[f"entity:{c.entity_id}" for c in identity_candidates],
                )
            ]
            decision_basis = top_reasons[0].signal
        else:
            top_reasons = [
                IdentityEvidence(
                    signal="no_identity_match",
                    strength=EvidenceStrength.SUPPORTING,
                    value=canonical_name,
                    detail="no candidates with matching identity evidence",
                )
            ]
            decision_basis = "no_candidate" if not identity_candidates else "all_different"

        metadata: dict[str, object] = {}
        if adjudication_result is not None:
            metadata["adjudication"] = adjudication_result.model_dump(mode="json")

        identity = IdentityResolutionResult(
            resolution=resolution,
            action=action,
            entity_id=None if matched_entity is None else matched_entity.id,
            candidates=identity_candidates,
            reasons=top_reasons,
            decision_basis=decision_basis,
            no_candidate=not identity_candidates,
            metadata=metadata,
        )

        legacy_candidates = [
            EntityCandidate(
                id=c.entity_id,
                canonical_name=c.canonical_name,
                status=c.status,
                class_keys=c.class_keys,
                match_reason=_legacy_match_reason(c),
            )
            for c in identity_candidates
        ]

        return ResolutionResult(
            outcome=to_legacy_resolution_outcome(resolution),
            entity=matched_entity,
            candidates=legacy_candidates,
            match_reason=decision_basis,
            identity=identity,
        )


def _multiple_same(candidates: list[IdentityCandidate]) -> bool:
    return sum(1 for c in candidates if c.decision == CandidateDecision.SAME) > 1


def _ambiguous_detail(candidates: list[IdentityCandidate]) -> str:
    same_n = sum(1 for c in candidates if c.decision == CandidateDecision.SAME)
    uncertain_n = sum(1 for c in candidates if c.decision == CandidateDecision.UNCERTAIN)
    return f"{len(candidates)} candidate(s); same={same_n}; uncertain={uncertain_n}"


def _legacy_match_reason(candidate: IdentityCandidate) -> str:
    """Prefer the strongest / most specific signal for the legacy candidate field."""
    if not candidate.reasons:
        return "unknown"
    decisive = [r for r in candidate.reasons if r.strength == EvidenceStrength.DECISIVE]
    chosen = decisive[0] if decisive else candidate.reasons[0]
    # Preserve historical near_name / alias / canonical_name / external_reference labels.
    signal = chosen.signal
    if signal == "external_reference_match":
        return "external_reference"
    if signal == "canonical_name_match":
        return "canonical_name"
    if signal == "alias_match":
        return "alias"
    return signal
