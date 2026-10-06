"""Supporting graph-context evidence for entity identity resolution (PR4)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from semantic_memory.identity.graph_predicates import (
    IDENTITY_GRAPH_PREDICATE_KEYS,
    IDENTITY_GRAPH_PREDICATE_SPECS,
    GraphEdgeDirection,
    IdentityGraphPredicateSpec,
)
from semantic_memory.models import Statement, StatementStatus
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.identity import EvidenceStrength, IdentityEvidence


@dataclass(frozen=True, slots=True)
class _GraphFact:
    predicate_key: str
    role: str
    object_entity_id: uuid.UUID
    statement_id: uuid.UUID
    valid_from: datetime | None
    valid_to: datetime | None


class IdentityGraphEvidenceService:
    """Collect allowlisted 1-hop graph facts and compare candidates conservatively."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ontology = OntologyRepository(session)
        self._predicate_ids: dict[str, uuid.UUID] | None = None
        self._predicate_keys: dict[uuid.UUID, str] | None = None

    def enrich_candidate_reasons(
        self,
        *,
        candidate_entity_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[IdentityEvidence]]:
        """Pairwise graph comparison only; never affects aggregate identity decisions."""
        if len(candidate_entity_ids) < 1:
            return {}

        self._ensure_predicate_maps()
        assert self._predicate_ids is not None

        facts_by_entity = {
            entity_id: self._load_facts(entity_id) for entity_id in candidate_entity_ids
        }

        out: dict[uuid.UUID, list[IdentityEvidence]] = {
            entity_id: [] for entity_id in candidate_entity_ids
        }
        for entity_id in candidate_entity_ids:
            accumulated: list[IdentityEvidence] = []
            for other_id in candidate_entity_ids:
                if other_id == entity_id:
                    continue
                accumulated.extend(
                    self._compare_fact_sets(
                        left=facts_by_entity[entity_id],
                        right=facts_by_entity[other_id],
                        left_entity_id=entity_id,
                        right_entity_id=other_id,
                    )
                )
            out[entity_id] = _dedupe_evidence(accumulated)

        return out

    def _ensure_predicate_maps(self) -> None:
        if self._predicate_ids is not None:
            return
        ids: dict[str, uuid.UUID] = {}
        keys: dict[uuid.UUID, str] = {}
        for key in IDENTITY_GRAPH_PREDICATE_KEYS:
            row = self._ontology.get_predicate_by_key(namespace_key="core", predicate_key=key)
            if row is not None:
                ids[key] = row.id
                keys[row.id] = key
        self._predicate_ids = ids
        self._predicate_keys = keys

    def _load_facts(self, entity_id: uuid.UUID) -> list[_GraphFact]:
        self._ensure_predicate_maps()
        assert self._predicate_ids is not None
        assert self._predicate_keys is not None
        if not self._predicate_ids:
            return []

        allowed_ids = set(self._predicate_ids.values())
        rows = list(
            self._session.scalars(
                select(Statement).where(
                    Statement.status == StatementStatus.ASSERTED.value,
                    Statement.predicate_id.in_(allowed_ids),
                    Statement.object_entity_id.is_not(None),
                    or_(
                        Statement.subject_entity_id == entity_id,
                        Statement.object_entity_id == entity_id,
                    ),
                )
            ).all()
        )

        facts: list[_GraphFact] = []
        seen_statement: set[uuid.UUID] = set()
        for statement in rows:
            if statement.id in seen_statement:
                continue
            predicate_key = self._predicate_keys.get(statement.predicate_id)
            if predicate_key is None:
                continue
            spec = IDENTITY_GRAPH_PREDICATE_SPECS[predicate_key]
            fact = self._statement_to_fact(
                entity_id=entity_id,
                statement=statement,
                spec=spec,
            )
            if fact is None:
                continue
            seen_statement.add(statement.id)
            facts.append(fact)
        return facts

    def _statement_to_fact(
        self,
        *,
        entity_id: uuid.UUID,
        statement: Statement,
        spec: IdentityGraphPredicateSpec,
    ) -> _GraphFact | None:
        assert statement.object_entity_id is not None
        if spec.direction == GraphEdgeDirection.OUTGOING:
            if statement.subject_entity_id != entity_id:
                return None
            neighbor_id = statement.object_entity_id
        else:
            if statement.subject_entity_id == entity_id:
                neighbor_id = statement.object_entity_id
            elif statement.object_entity_id == entity_id:
                neighbor_id = statement.subject_entity_id
            else:
                return None

        return _GraphFact(
            predicate_key=spec.predicate_key,
            role=spec.role.value,
            object_entity_id=neighbor_id,
            statement_id=statement.id,
            valid_from=statement.valid_from,
            valid_to=statement.valid_to,
        )

    def _compare_fact_sets(
        self,
        *,
        left: list[_GraphFact],
        right: list[_GraphFact],
        left_entity_id: uuid.UUID,
        right_entity_id: uuid.UUID,
    ) -> list[IdentityEvidence]:
        evidence: list[IdentityEvidence] = []
        right_by_predicate: dict[str, list[_GraphFact]] = {}
        for fact in right:
            right_by_predicate.setdefault(fact.predicate_key, []).append(fact)

        matched_right: set[tuple[str, uuid.UUID, uuid.UUID]] = set()

        for left_fact in left:
            peers = right_by_predicate.get(left_fact.predicate_key, [])
            shared = [
                peer
                for peer in peers
                if peer.object_entity_id == left_fact.object_entity_id
            ]
            if shared:
                for peer in shared:
                    matched_right.add((peer.predicate_key, peer.object_entity_id, peer.statement_id))
                evidence.append(
                    IdentityEvidence(
                        signal="shared_neighbor",
                        strength=EvidenceStrength.SUPPORTING,
                        namespace=left_fact.predicate_key,
                        value=str(left_fact.object_entity_id),
                        predicate=left_fact.role,
                        object_entity_id=left_fact.object_entity_id,
                        detail=(
                            f"shared {left_fact.role} with comparison entity "
                            f"{right_entity_id}"
                        ),
                        evidence_refs=_statement_refs(left_fact, shared),
                    )
                )
                continue

            conflicts = [
                peer
                for peer in peers
                if peer.object_entity_id != left_fact.object_entity_id
            ]
            for peer in conflicts:
                matched_right.add((peer.predicate_key, peer.object_entity_id, peer.statement_id))
                refs = _statement_refs(left_fact, [peer])
                evidence.append(
                    IdentityEvidence(
                        signal="conflicting_neighbor",
                        strength=EvidenceStrength.SUPPORTING,
                        namespace=left_fact.predicate_key,
                        value=str(left_fact.object_entity_id),
                        predicate=left_fact.role,
                        object_entity_id=left_fact.object_entity_id,
                        detail=(
                            f"different {left_fact.role} vs comparison entity "
                            f"{right_entity_id} ({peer.object_entity_id})"
                        ),
                        evidence_refs=refs,
                    )
                )
                if _intervals_overlap(
                    left_fact.valid_from,
                    left_fact.valid_to,
                    peer.valid_from,
                    peer.valid_to,
                ):
                    evidence.append(
                        IdentityEvidence(
                            signal="temporal_conflict",
                            strength=EvidenceStrength.SUPPORTING,
                            namespace=left_fact.predicate_key,
                            value=str(left_fact.object_entity_id),
                            predicate=left_fact.role,
                            object_entity_id=left_fact.object_entity_id,
                            detail="overlapping validity intervals for different neighbors",
                            evidence_refs=refs,
                        )
                    )

        _ = matched_right
        _ = left_entity_id
        return evidence


def _statement_refs(left: _GraphFact, others: list[_GraphFact]) -> list[str]:
    ids = {left.statement_id, *(item.statement_id for item in others)}
    return [f"statement:{item}" for item in sorted(ids, key=lambda value: value.int)]


def _intervals_overlap(
    a_from: datetime | None,
    a_to: datetime | None,
    b_from: datetime | None,
    b_to: datetime | None,
) -> bool:
    """True when two validity intervals intersect (open bounds allowed)."""

    def _start(value: datetime | None) -> datetime | None:
        return value

    def _end(value: datetime | None) -> datetime | None:
        return value

    start_a, end_a = _start(a_from), _end(a_to)
    start_b, end_b = _start(b_from), _end(b_to)
    if end_a is not None and start_b is not None and end_a < start_b:
        return False
    if end_b is not None and start_a is not None and end_b < start_a:
        return False
    return True


def _dedupe_evidence(items: list[IdentityEvidence]) -> list[IdentityEvidence]:
    seen: set[tuple[str, str | None, str | None, str | None, tuple[str, ...]]] = set()
    out: list[IdentityEvidence] = []
    for item in items:
        key = (
            item.signal,
            item.namespace,
            item.value,
            item.predicate,
            tuple(sorted(item.evidence_refs)),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def sort_candidates_for_explanation(
    candidates: list,
) -> list:
    """Order UNCERTAIN candidates by shared graph evidence count (explanation only)."""

    def _graph_support_count(candidate: object) -> int:
        reasons = getattr(candidate, "reasons", [])
        return sum(
            1
            for reason in reasons
            if reason.signal
            in {"shared_neighbor", "attribute_overlap", "conflicting_neighbor", "temporal_conflict"}
        )

    return sorted(
        candidates,
        key=lambda candidate: (
            -_graph_support_count(candidate),
            str(getattr(candidate, "entity_id", "")),
        ),
    )
