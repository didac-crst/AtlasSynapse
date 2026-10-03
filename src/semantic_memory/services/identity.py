"""Deterministic entity identity resolution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from semantic_memory.models import Entity
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.schemas.entities import (
    EntityCandidate,
    ResolutionOutcome,
)


@dataclass
class ResolutionResult:
    outcome: ResolutionOutcome
    entity: Entity | None = None
    candidates: list[EntityCandidate] = field(default_factory=list)
    match_reason: str | None = None


class IdentityService:
    """Resolve entity identity without silent merges."""

    def __init__(self, session: Session) -> None:
        self._entities = EntityRepository(session)

    def resolve(
        self,
        *,
        canonical_name: str,
        class_id: uuid.UUID | None = None,
        external_source_system: str | None = None,
        external_id: str | None = None,
    ) -> ResolutionResult:
        # 1. External reference exact match
        if external_source_system and external_id:
            matched = self._entities.find_by_external_reference(
                source_system=external_source_system,
                external_id=external_id,
            )
            if matched is not None:
                return ResolutionResult(
                    outcome=ResolutionOutcome.REUSE,
                    entity=matched,
                    match_reason="external_reference",
                )

        # 2. Canonical name exact match (indexed normalized SQL, class join)
        canonical_matches = self._entities.find_by_canonical_name(canonical_name, class_id=class_id)
        if len(canonical_matches) == 1:
            return ResolutionResult(
                outcome=ResolutionOutcome.REUSE,
                entity=canonical_matches[0],
                match_reason="canonical_name",
            )
        if len(canonical_matches) > 1:
            return ResolutionResult(
                outcome=ResolutionOutcome.AMBIGUOUS,
                candidates=self._to_candidates(canonical_matches, "canonical_name"),
            )

        # 3. Alias exact match (indexed normalized_alias, class join)
        alias_matches = self._entities.find_by_alias(canonical_name, class_id=class_id)
        if len(alias_matches) == 1:
            return ResolutionResult(
                outcome=ResolutionOutcome.REUSE,
                entity=alias_matches[0],
                match_reason="alias",
            )
        if len(alias_matches) > 1:
            return ResolutionResult(
                outcome=ResolutionOutcome.AMBIGUOUS,
                candidates=self._to_candidates(alias_matches, "alias"),
            )

        # 4. Candidate discovery (class-scoped structural signals)
        discovered = self._entities.find_candidates(name=canonical_name, class_id=class_id)
        if len(discovered) == 1:
            entity, reason = discovered[0]
            return ResolutionResult(
                outcome=ResolutionOutcome.REUSE,
                entity=entity,
                match_reason=reason,
            )
        if len(discovered) > 1:
            candidates = [self._to_candidate(entity, reason) for entity, reason in discovered]
            return ResolutionResult(
                outcome=ResolutionOutcome.AMBIGUOUS,
                candidates=candidates,
            )

        return ResolutionResult(outcome=ResolutionOutcome.CREATE)

    def _to_candidates(self, entities: list[Entity], reason: str) -> list[EntityCandidate]:
        return [self._to_candidate(entity, reason) for entity in entities]

    def _to_candidate(self, entity: Entity, reason: str) -> EntityCandidate:
        types = self._entities.list_types(entity.id)
        return EntityCandidate(
            id=entity.id,
            canonical_name=entity.canonical_name,
            status=entity.status,  # type: ignore[arg-type]
            class_keys=[ontology_class.key for ontology_class, _ns in types],
            match_reason=reason,
        )
