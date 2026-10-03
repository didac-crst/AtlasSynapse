"""Optional embedding index service.

Embeddings are derived candidates only. They never determine truth, identity,
or ontology acceptance. Deleting or rebuilding them leaves canonical rows
unchanged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.models import (
    Entity,
    OntologyClass,
    OntologyNamespace,
    OntologyPredicate,
)
from semantic_memory.models.enums import EmbeddingObjectType, GateDecision, ProposalType
from semantic_memory.repositories.embeddings import EmbeddingRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.services.embedding_providers import (
    DisabledEmbeddingProvider,
    EmbeddingProvider,
    MockEmbeddingProvider,
    cosine_similarity,
)
from semantic_memory.services.gates import GateOutcome


@dataclass(frozen=True)
class SimilarityCandidate:
    object_type: EmbeddingObjectType
    object_id: uuid.UUID
    key: str
    score: float
    label: str | None = None


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    cfg = settings or get_settings()
    if cfg.embedding_mode == "mock":
        return MockEmbeddingProvider()
    return DisabledEmbeddingProvider()


class EmbeddingService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        if provider is None:
            self._provider = build_embedding_provider(self._settings)
        else:
            self._provider = provider
        self._embeddings = EmbeddingRepository(session)
        self._ontology = OntologyRepository(session)

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    @property
    def enabled(self) -> bool:
        return not isinstance(self._provider, DisabledEmbeddingProvider)

    def upsert_text(
        self,
        *,
        object_type: EmbeddingObjectType,
        object_id: uuid.UUID,
        text: str,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        if not self.enabled:
            return
        embedded = self._provider.embed(text)
        self._embeddings.upsert(
            object_type=object_type,
            object_id=object_id,
            model_key=embedded.model_key,
            dimensions=len(embedded.vector),
            vector=embedded.vector,
            content_hash=embedded.content_hash,
            created_by_actor_id=actor_id,
        )

    def delete_for_object(
        self,
        *,
        object_type: EmbeddingObjectType,
        object_id: uuid.UUID,
    ) -> int:
        return self._embeddings.delete_for_object(
            object_type=object_type,
            object_id=object_id,
            model_key=None if not self.enabled else self._provider.model_key,
        )

    def delete_all(self) -> int:
        return self._embeddings.delete_all(
            model_key=None if not self.enabled else self._provider.model_key
        )

    def count(self) -> int:
        return self._embeddings.count()

    def rebuild_ontology_embeddings(self, *, namespace_key: str = "core") -> int:
        """Rebuild class/predicate embeddings. Does not mutate ontology rows."""
        if not self.enabled:
            return 0
        rebuilt = 0
        class_rows = list(
            self._session.scalars(
                select(OntologyClass)
                .join(OntologyNamespace, OntologyNamespace.id == OntologyClass.namespace_id)
                .where(OntologyNamespace.key == namespace_key)
            ).all()
        )
        for class_row in class_rows:
            class_revision = self._ontology.get_current_class_revision(class_row)
            label = class_revision.label if class_revision is not None else class_row.key
            description = None if class_revision is None else class_revision.description
            text = f"class {class_row.key} {label} {description or ''}".strip()
            self.upsert_text(
                object_type=EmbeddingObjectType.CLASS,
                object_id=class_row.id,
                text=text,
            )
            rebuilt += 1
        predicate_rows = list(
            self._session.scalars(
                select(OntologyPredicate)
                .join(OntologyNamespace, OntologyNamespace.id == OntologyPredicate.namespace_id)
                .where(OntologyNamespace.key == namespace_key)
            ).all()
        )
        for predicate_row in predicate_rows:
            predicate_revision = self._ontology.get_current_predicate_revision(predicate_row)
            label = (
                predicate_revision.label if predicate_revision is not None else predicate_row.key
            )
            description = None if predicate_revision is None else predicate_revision.description
            text = f"predicate {predicate_row.key} {label} {description or ''}".strip()
            self.upsert_text(
                object_type=EmbeddingObjectType.PREDICATE,
                object_id=predicate_row.id,
                text=text,
            )
            rebuilt += 1
        return rebuilt

    def rebuild_entity_embeddings(self) -> int:
        if not self.enabled:
            return 0
        rebuilt = 0
        for entity in self._session.scalars(select(Entity)).all():
            text = f"entity {entity.canonical_name}"
            self.upsert_text(
                object_type=EmbeddingObjectType.ENTITY,
                object_id=entity.id,
                text=text,
            )
            rebuilt += 1
        return rebuilt

    def find_similar(
        self,
        *,
        object_type: EmbeddingObjectType,
        text: str,
        limit: int = 5,
        min_score: float = 0.75,
    ) -> list[SimilarityCandidate]:
        if not self.enabled:
            return []
        query = self._provider.embed(text)
        rows = self._embeddings.list_by_type(
            object_type=object_type, model_key=self._provider.model_key
        )
        scored: list[SimilarityCandidate] = []
        for row in rows:
            vector = [float(item) for item in row.vector]
            score = cosine_similarity(query.vector, vector)
            if score < min_score:
                continue
            key, label = self._resolve_label(object_type, row.object_id)
            scored.append(
                SimilarityCandidate(
                    object_type=object_type,
                    object_id=row.object_id,
                    key=key,
                    score=score,
                    label=label,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def make_similarity_gate(self) -> Any:
        """Advisory gate: similarity never accepts or fails a proposal alone."""

        def _gate(proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome | None:
            if not self.enabled:
                return GateOutcome(
                    "similarity",
                    GateDecision.PASS,
                    {"enabled": False, "reason": "embeddings_disabled"},
                )
            object_type, text, proposed_key = _proposal_similarity_target(proposal_type, payload)
            if object_type is None or text is None:
                return GateOutcome("similarity", GateDecision.PASS, {"skipped": True})
            candidates = self.find_similar(object_type=object_type, text=text, limit=5)
            # Ignore self-key collisions handled by existing_key.
            filtered = [item for item in candidates if item.key != proposed_key]
            if not filtered:
                return GateOutcome("similarity", GateDecision.PASS, {"candidates": []})
            top = filtered[0]
            details = {
                "candidates": [
                    {
                        "object_type": item.object_type.value,
                        "object_id": str(item.object_id),
                        "key": item.key,
                        "score": item.score,
                        "label": item.label,
                    }
                    for item in filtered
                ]
            }
            if top.score >= 0.95:
                return GateOutcome(
                    "similarity",
                    GateDecision.REUSE_RECOMMENDED,
                    {**details, "reason": "high_similarity_candidate"},
                )
            return GateOutcome(
                "similarity",
                GateDecision.MANUAL_REVIEW,
                {**details, "reason": "similar_candidates"},
            )

        return _gate

    def _resolve_label(
        self, object_type: EmbeddingObjectType, object_id: uuid.UUID
    ) -> tuple[str, str | None]:
        if object_type == EmbeddingObjectType.CLASS:
            row = self._session.get(OntologyClass, object_id)
            if row is None:
                return str(object_id), None
            revision = self._ontology.get_current_class_revision(row)
            return row.key, None if revision is None else revision.label
        if object_type == EmbeddingObjectType.PREDICATE:
            row_p = self._session.get(OntologyPredicate, object_id)
            if row_p is None:
                return str(object_id), None
            revision_p = self._ontology.get_current_predicate_revision(row_p)
            return row_p.key, None if revision_p is None else revision_p.label
        entity = self._session.get(Entity, object_id)
        if entity is None:
            return str(object_id), None
        return entity.canonical_name, entity.canonical_name


def _proposal_similarity_target(
    proposal_type: ProposalType, payload: dict[str, Any]
) -> tuple[EmbeddingObjectType | None, str | None, str | None]:
    if proposal_type == ProposalType.CLASS:
        key = str(payload.get("key") or "")
        label = str(payload.get("label") or key)
        description = str(payload.get("description") or "")
        return (
            EmbeddingObjectType.CLASS,
            f"class {key} {label} {description}".strip(),
            key,
        )
    if proposal_type == ProposalType.PREDICATE:
        key = str(payload.get("key") or "")
        label = str(payload.get("label") or key)
        description = str(payload.get("description") or "")
        return (
            EmbeddingObjectType.PREDICATE,
            f"predicate {key} {label} {description}".strip(),
            key,
        )
    return None, None, None
