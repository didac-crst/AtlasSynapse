"""Database-backed semantic retrieval with transparent ranking signals.

Works without pgvector or an LLM. Vector similarity may contribute an optional
signal later; it never determines truth or identity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from semantic_memory.exceptions import UnknownEntityError, ValidationFailedError
from semantic_memory.models import (
    Entity,
    EntityAlias,
    EntityStatus,
    EntityType,
    OntologyClass,
    OntologyNamespace,
    OntologyPredicate,
    Source,
    Statement,
    StatementEvidence,
    StatementStatus,
)
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.retrieval import (
    MemoryHitType,
    NeighborhoodEdge,
    NeighborhoodResponse,
    RankedEntityHit,
    RankedStatementHit,
    RankingSignals,
    RelevantContextRequest,
    RelevantContextResponse,
    SearchEntitiesRequest,
    SearchEntitiesResponse,
    SearchSemanticMemoryRequest,
    SearchSemanticMemoryResponse,
    SearchStatementsRequest,
    SearchStatementsResponse,
    SemanticMemoryHit,
    TemporalState,
)
from semantic_memory.services.conflicts import ConflictService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.statements import StatementService


def _ilike_contains(query: str) -> str:
    escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class RetrievalService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._entities = EntityRepository(session)
        self._statements = StatementRepository(session)
        self._ontology = OntologyRepository(session)
        self._entity_service = EntityService(session)
        self._statement_service = StatementService(session)
        self._provenance = ProvenanceService(session)
        self._conflicts = ConflictService(session)

    def search_entities(self, request: SearchEntitiesRequest) -> SearchEntitiesResponse:
        pattern = _ilike_contains(request.query)
        stmt = (
            select(Entity)
            .outerjoin(EntityAlias, EntityAlias.entity_id == Entity.id)
            .outerjoin(EntityType, EntityType.entity_id == Entity.id)
            .outerjoin(OntologyClass, OntologyClass.id == EntityType.class_id)
            .outerjoin(OntologyNamespace, OntologyNamespace.id == OntologyClass.namespace_id)
            .where(
                or_(
                    Entity.canonical_name.ilike(pattern, escape="\\"),
                    EntityAlias.alias.ilike(pattern, escape="\\"),
                    EntityAlias.normalized_alias.ilike(pattern, escape="\\"),
                )
            )
            .distinct()
            .limit(request.limit * 3)
        )
        if request.status is not None:
            stmt = stmt.where(Entity.status == request.status.value)
        if request.class_key is not None:
            stmt = stmt.where(
                OntologyClass.key == request.class_key,
                OntologyNamespace.key == request.namespace_key,
            )
        rows = list(self._session.scalars(stmt).all())
        now = request.as_of or datetime.now(UTC)
        hits: list[RankedEntityHit] = []
        for entity in rows:
            signals, reasons = self._score_entity(entity, query=request.query, now=now)
            hits.append(
                RankedEntityHit(
                    entity=self._entity_service.get(entity.id),
                    signals=signals,
                    ranking_score=signals.ranking_score,
                    match_reasons=reasons,
                )
            )
        hits.sort(key=lambda item: item.ranking_score, reverse=True)
        hits = hits[: request.limit]
        return SearchEntitiesResponse(
            query=request.query,
            hits=hits,
            ranking_explanations=[
                "Ranking combines lexical relevance, recency, and ontology specificity.",
                "ranking_score is for ordering only and is not a truth score.",
            ],
        )

    def search_statements(self, request: SearchStatementsRequest) -> SearchStatementsResponse:
        empty = SearchStatementsResponse(
            query=request.query,
            total=0,
            limit=request.limit,
            offset=request.offset,
            hits=[],
            ranking_explanations=[
                "Results are ordered by created_at DESC, id DESC for stable pagination.",
                "ranking_score is for display only and does not affect page order.",
            ],
        )
        stmt = select(Statement)
        if request.status is not None:
            stmt = stmt.where(Statement.status == request.status.value)
        if request.subject_entity_id is not None:
            identity_ids = self._entities.identity_group_ids(request.subject_entity_id)
            stmt = stmt.where(Statement.subject_entity_id.in_(identity_ids))
        if request.object_entity_id is not None:
            identity_ids = self._entities.identity_group_ids(request.object_entity_id)
            stmt = stmt.where(Statement.object_entity_id.in_(identity_ids))
        if request.entity_id is not None:
            identity_ids = self._entities.identity_group_ids(request.entity_id)
            stmt = stmt.where(
                or_(
                    Statement.subject_entity_id.in_(identity_ids),
                    Statement.object_entity_id.in_(identity_ids),
                )
            )
        if request.predicate_key is not None:
            predicate = self._ontology.get_predicate_by_key(
                namespace_key=request.namespace_key or "core",
                predicate_key=request.predicate_key,
            )
            if predicate is None:
                return empty.model_copy(
                    update={
                        "ranking_explanations": [
                            "Unknown predicate_key; no statements matched."
                        ]
                    }
                )
            stmt = stmt.where(Statement.predicate_id == predicate.id)
        elif request.namespace_key is not None:
            stmt = (
                stmt.join(OntologyPredicate, OntologyPredicate.id == Statement.predicate_id)
                .join(
                    OntologyNamespace,
                    OntologyNamespace.id == OntologyPredicate.namespace_id,
                )
                .where(OntologyNamespace.key == request.namespace_key)
            )
        if request.temporal_state is not None:
            stmt = self._apply_temporal_state_filter(stmt, request.temporal_state)
        if request.query:
            pattern = _ilike_contains(request.query)
            stmt = stmt.where(
                or_(
                    Statement.object_string.ilike(pattern, escape="\\"),
                    Statement.normalized_object.ilike(pattern, escape="\\"),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int(self._session.scalar(count_stmt) or 0)

        stmt = (
            stmt.order_by(Statement.created_at.desc(), Statement.id.desc())
            .limit(request.limit)
            .offset(request.offset)
        )
        rows = list(self._session.scalars(stmt).all())
        now = request.as_of or datetime.now(UTC)
        focus = (
            request.subject_entity_id
            or request.object_entity_id
            or request.entity_id
        )
        hits: list[RankedStatementHit] = []
        for statement in rows:
            signals, reasons = self._score_statement(
                statement, query=request.query, focus_entity_id=focus, now=now
            )
            hits.append(
                RankedStatementHit(
                    statement=self._statement_service.get(statement.id),
                    signals=signals,
                    ranking_score=signals.ranking_score,
                    match_reasons=reasons,
                )
            )
        return SearchStatementsResponse(
            query=request.query,
            total=total,
            limit=request.limit,
            offset=request.offset,
            hits=hits,
            ranking_explanations=[
                "Results are ordered by created_at DESC, id DESC for stable pagination.",
                (
                    "ranking_score reflects lexical match, temporal validity, "
                    "recency, evidence, and reliability but does not reorder pages."
                ),
                "ranking_score is for display only and is not a truth score.",
                (
                    "null/null valid_from/valid_to means temporally unspecified "
                    "(use temporal_state=unbounded); it is not 'valid forever'."
                ),
            ],
        )

    @staticmethod
    def _apply_temporal_state_filter(
        stmt: Select[Any], state: TemporalState
    ) -> Select[Any]:
        if state == TemporalState.BOUNDED:
            return stmt.where(
                Statement.valid_from.is_not(None),
                Statement.valid_to.is_not(None),
            )
        if state == TemporalState.OPEN_END:
            return stmt.where(
                Statement.valid_from.is_not(None),
                Statement.valid_to.is_(None),
            )
        if state == TemporalState.OPEN_START:
            return stmt.where(
                Statement.valid_from.is_(None),
                Statement.valid_to.is_not(None),
            )
        return stmt.where(
            Statement.valid_from.is_(None),
            Statement.valid_to.is_(None),
        )

    def get_entity_neighborhood(
        self,
        entity_id: uuid.UUID,
        *,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> NeighborhoodResponse:
        entity = self._entities.get(entity_id)
        if entity is None:
            raise UnknownEntityError(
                f"Entity {entity_id} was not found",
                details={"entity_id": str(entity_id)},
            )
        # Statement FKs are preserved on merge; reads follow the identity group.
        identity_ids = set(self._entities.identity_group_ids(entity_id))
        survivor_id = self._entities.resolve_survivor_id(entity_id)
        statements = self._statements.list_for_entity_timeline(list(identity_ids))
        now = as_of or datetime.now(UTC)
        edges: list[NeighborhoodEdge] = []
        seen_statement_ids: set[uuid.UUID] = set()
        for statement in statements:
            if statement.status != StatementStatus.ASSERTED.value:
                continue
            if statement.id in seen_statement_ids:
                continue
            seen_statement_ids.add(statement.id)
            subject_in_group = statement.subject_entity_id in identity_ids
            object_in_group = (
                statement.object_entity_id is not None
                and statement.object_entity_id in identity_ids
            )
            if subject_in_group and not object_in_group:
                direction = "outgoing"
                neighbor = statement.object_entity_id
            elif object_in_group and not subject_in_group:
                direction = "incoming"
                neighbor = statement.subject_entity_id
            else:
                # Self-loop within the merged identity group.
                direction = "outgoing"
                neighbor = survivor_id
            if neighbor is not None:
                neighbor = self._entities.resolve_survivor_id(neighbor)
            signals, _ = self._score_statement(
                statement, query=None, focus_entity_id=survivor_id, now=now
            )
            edges.append(
                NeighborhoodEdge(
                    statement=self._statement_service.get(statement.id),
                    direction=direction,
                    neighbor_entity_id=neighbor,
                    signals=signals,
                    ranking_score=signals.ranking_score,
                )
            )
        edges.sort(key=lambda item: item.ranking_score, reverse=True)
        return NeighborhoodResponse(
            entity_id=survivor_id if entity_id in identity_ids else entity_id,
            edges=edges[:limit],
            ranking_explanations=[
                "Neighborhood edges are ordered by temporal validity, recency, and evidence.",
                "Merged entity aliases are included; neighbor ids resolve to surviving entities.",
                "ranking_score is for ordering only and is not a truth score.",
            ],
        )

    def search_semantic_memory(
        self, request: SearchSemanticMemoryRequest
    ) -> SearchSemanticMemoryResponse:
        entity_hits = self.search_entities(
            SearchEntitiesRequest(
                query=request.query,
                class_key=request.class_key,
                namespace_key=request.namespace_key,
                as_of=request.as_of,
                limit=request.limit,
            )
        )
        statement_hits = self.search_statements(
            SearchStatementsRequest(
                query=request.query,
                entity_id=request.entity_id,
                as_of=request.as_of,
                limit=request.limit,
            )
        )
        hits: list[SemanticMemoryHit] = []
        for entity_hit in entity_hits.hits:
            hits.append(
                SemanticMemoryHit(
                    hit_type=MemoryHitType.ENTITY,
                    entity=entity_hit.entity,
                    signals=entity_hit.signals,
                    ranking_score=entity_hit.ranking_score,
                    match_reasons=entity_hit.match_reasons,
                )
            )
        for statement_hit in statement_hits.hits:
            hits.append(
                SemanticMemoryHit(
                    hit_type=MemoryHitType.STATEMENT,
                    statement=statement_hit.statement,
                    signals=statement_hit.signals,
                    ranking_score=statement_hit.ranking_score,
                    match_reasons=statement_hit.match_reasons,
                )
            )
        if request.include_conflicts and request.entity_id is not None:
            conflicts = self._conflicts.find_conflicts(entity_id=request.entity_id)
            for conflict in conflicts.conflicts:
                signals = RankingSignals(
                    lexical_relevance=0.2,
                    evidence_presence=0.5,
                    notes=["Conflict metadata included for context completeness."],
                )
                hits.append(
                    SemanticMemoryHit(
                        hit_type=MemoryHitType.CONFLICT,
                        conflict=conflict,
                        signals=signals,
                        ranking_score=signals.ranking_score,
                        match_reasons=["open_or_matching_conflict"],
                    )
                )
        hits.sort(key=lambda item: item.ranking_score, reverse=True)
        return SearchSemanticMemoryResponse(
            query=request.query,
            hits=hits[: request.limit],
            ranking_explanations=[
                "Semantic memory search merges entity, statement, and optional conflict hits.",
                "Vector search was not required and was not used.",
                "ranking_score is for ordering only and is not a truth score.",
            ],
            vector_search_used=False,
        )

    def get_relevant_context(self, request: RelevantContextRequest) -> RelevantContextResponse:
        entity = None
        timeline = None
        neighborhood = None
        statements: list[RankedStatementHit] = []
        conflicts = []
        explanation = None
        ranking_explanations = [
            "Relevant context composes existing deterministic retrieval primitives.",
            "No LLM or vector provider is required.",
        ]

        if request.statement_id is not None:
            explanation = self._provenance.explain_statement(request.statement_id)
            statement = self._statement_service.get(request.statement_id)
            subject_id = statement.subject_entity_id
            entity = self._entity_service.get(subject_id)
            timeline = self._statement_service.get_timeline(subject_id)
            neighborhood = self.get_entity_neighborhood(subject_id, as_of=request.as_of)
            conflicts = self._conflicts.find_conflicts(statement_id=request.statement_id).conflicts

        if request.entity_id is not None:
            entity = self._entity_service.get(request.entity_id)
            timeline = self._statement_service.get_timeline(request.entity_id)
            neighborhood = self.get_entity_neighborhood(request.entity_id, as_of=request.as_of)
            conflicts = self._conflicts.find_conflicts(entity_id=request.entity_id).conflicts
            statements = self.search_statements(
                SearchStatementsRequest(
                    entity_id=request.entity_id,
                    as_of=request.as_of,
                    limit=request.limit,
                )
            ).hits

        if request.query:
            memory = self.search_semantic_memory(
                SearchSemanticMemoryRequest(
                    query=request.query,
                    entity_id=request.entity_id,
                    namespace_key=request.namespace_key,
                    as_of=request.as_of,
                    limit=request.limit,
                )
            )
            ranking_explanations.extend(memory.ranking_explanations)
            if not statements:
                statements = [
                    RankedStatementHit(
                        statement=hit.statement,
                        signals=hit.signals,
                        ranking_score=hit.ranking_score,
                        match_reasons=hit.match_reasons,
                    )
                    for hit in memory.hits
                    if hit.statement is not None
                ]
            if entity is None:
                for hit in memory.hits:
                    if hit.entity is not None:
                        entity = hit.entity
                        break

        if request.entity_id is None and request.statement_id is None and not request.query:
            raise ValidationFailedError(
                "Relevant context requires query, entity_id, or statement_id",
                details={},
            )

        return RelevantContextResponse(
            entity=entity,
            timeline=timeline,
            neighborhood=neighborhood,
            statements=statements[: request.limit],
            conflicts=conflicts,
            explanation=explanation,
            ranking_explanations=ranking_explanations,
            metadata={"vector_search_used": False, "llm_used": False},
        )

    def _score_entity(
        self, entity: Entity, *, query: str, now: datetime
    ) -> tuple[RankingSignals, list[str]]:
        notes: list[str] = []
        reasons: list[str] = []
        q = query.casefold().strip()
        name = entity.canonical_name.casefold()
        if name == q:
            lexical = 1.0
            reasons.append("exact_canonical_name")
            notes.append("Exact canonical name match.")
        elif q in name:
            lexical = 0.8
            reasons.append("canonical_name_contains")
            notes.append("Canonical name contains query.")
        else:
            lexical = 0.45
            reasons.append("alias_or_partial_match")
            notes.append("Matched via alias or partial lexical overlap.")

        age_seconds = max(0.0, (now - entity.updated_at).total_seconds())
        recency = _clamp(1.0 - (age_seconds / (86400.0 * 365.0)))
        notes.append("Recency decays over about one year from updated_at.")

        type_count = self._session.scalar(
            select(func.count()).select_from(EntityType).where(EntityType.entity_id == entity.id)
        )
        specificity = _clamp(0.3 + 0.2 * float(type_count or 0))
        notes.append("Ontology specificity increases with asserted entity types.")

        signals = RankingSignals(
            lexical_relevance=lexical,
            temporal_validity=1.0 if entity.status == EntityStatus.ACTIVE.value else 0.2,
            recency=recency,
            evidence_presence=0.0,
            source_reliability=0.0,
            entity_proximity=0.0,
            ontology_specificity=specificity,
            notes=notes,
        )
        return signals, reasons

    def _score_statement(
        self,
        statement: Statement,
        *,
        query: str | None,
        focus_entity_id: uuid.UUID | None,
        now: datetime,
    ) -> tuple[RankingSignals, list[str]]:
        notes: list[str] = []
        reasons: list[str] = []

        lexical = 0.0
        if query:
            q = query.casefold()
            haystacks = [
                (statement.object_string or "").casefold(),
                (statement.normalized_object or "").casefold(),
            ]
            if any(h == q for h in haystacks if h):
                lexical = 1.0
                reasons.append("exact_object_text")
            elif any(q in h for h in haystacks if h):
                lexical = 0.75
                reasons.append("object_text_contains")
            else:
                lexical = 0.2
                reasons.append("structural_match")
        else:
            lexical = 0.4
            reasons.append("entity_linked_statement")

        temporal = self._temporal_validity(statement, now)
        notes.append("Temporal validity is 1 when as_of is inside valid_from/valid_to.")

        age_seconds = max(0.0, (now - statement.asserted_at).total_seconds())
        recency = _clamp(1.0 - (age_seconds / (86400.0 * 365.0)))
        notes.append("Recency decays over about one year from asserted_at.")

        evidence_count = self._session.scalar(
            select(func.count())
            .select_from(StatementEvidence)
            .where(StatementEvidence.statement_id == statement.id)
        )
        evidence_presence = 1.0 if (evidence_count or 0) > 0 else 0.0
        if evidence_presence:
            reasons.append("has_evidence")

        reliability = self._max_source_reliability(statement.id)
        notes.append("Source reliability uses the max reliability among linked sources.")

        proximity = 0.0
        if focus_entity_id is not None:
            if (
                statement.subject_entity_id == focus_entity_id
                or statement.object_entity_id == focus_entity_id
            ):
                proximity = 1.0
                reasons.append("focus_entity_adjacent")
                notes.append("Statement is adjacent to the focus entity.")

        predicate = self._session.get(OntologyPredicate, statement.predicate_id)
        specificity = 0.5
        if predicate is not None:
            revision = self._ontology.get_current_predicate_revision(predicate)
            domains = (
                0 if revision is None else len(self._ontology.list_domain_class_ids(revision.id))
            )
            ranges = (
                0 if revision is None else len(self._ontology.list_range_class_ids(revision.id))
            )
            specificity = _clamp(0.3 + 0.15 * (domains + ranges))
            notes.append("Ontology specificity rises with domain/range constraints.")

        signals = RankingSignals(
            lexical_relevance=lexical,
            temporal_validity=temporal,
            recency=recency,
            evidence_presence=evidence_presence,
            source_reliability=reliability,
            entity_proximity=proximity,
            ontology_specificity=specificity,
            notes=notes,
        )
        return signals, reasons

    def _temporal_validity(self, statement: Statement, now: datetime) -> float:
        if statement.valid_from is None and statement.valid_to is None:
            return 0.7
        if statement.valid_from is not None and now < statement.valid_from:
            return 0.2
        if statement.valid_to is not None and now > statement.valid_to:
            return 0.2
        return 1.0

    def _max_source_reliability(self, statement_id: uuid.UUID) -> float:
        value = self._session.scalar(
            select(func.max(Source.reliability))
            .select_from(StatementEvidence)
            .join(Source, Source.id == StatementEvidence.source_id)
            .where(StatementEvidence.statement_id == statement_id)
        )
        if value is None:
            return 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
