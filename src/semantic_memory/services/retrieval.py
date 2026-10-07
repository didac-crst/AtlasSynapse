"""Database-backed semantic retrieval with transparent ranking signals.

Works without pgvector or an LLM. Vector similarity may contribute an optional
signal later; it never determines truth or identity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, aliased

from semantic_memory.exceptions import UnknownEntityError, ValidationFailedError
from semantic_memory.models import (
    Entity,
    EntityAlias,
    EntityStatus,
    EntityType,
    OntologyClass,
    OntologyNamespace,
    OntologyPredicate,
    Statement,
    StatementStatus,
)
from semantic_memory.observability.request_context import set_last_timings
from semantic_memory.observability.timing import RequestTimer
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.repositories.provenance import ProvenanceRepository
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
from semantic_memory.services.lexical import (
    lexical_match_patterns,
    lexical_tokens,
    score_lexical_relevance,
)
from semantic_memory.services.predicate_intent import (
    PredicateLexicon,
    build_predicate_lexicon,
    score_predicate_intent,
    top_intent_predicates,
)
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.statements import StatementService
from semantic_memory.services.temporal_intent import (
    TemporalIntent,
    detect_temporal_intent,
    score_temporal_validity,
)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


# Bounded 1-hop anchor expansion (not a general graph walker).
_MAX_ANCHOR_ENTITIES = 3
_MIN_ANCHOR_LEXICAL = 0.75
_MAX_STATEMENTS_PER_ANCHOR = 40
_MAX_ANCHOR_STATEMENTS_TOTAL = 80
_ANCHOR_LEXICAL_FLOOR = 0.62
_STRONG_ANCHOR_REASONS = frozenset(
    {
        "exact_text",
        "exact_token_name",
        # Sparse multi-term still means the entity name equals a query token —
        # the structural blind-spot case (Didac in "Didac previous Airbus role").
        "exact_token_name_sparse",
        "all_tokens_present",
        "phrase_contains",
    }
)


class RetrievalService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._entities = EntityRepository(session)
        self._statements = StatementRepository(session)
        self._ontology = OntologyRepository(session)
        self._provenance_repo = ProvenanceRepository(session)
        self._entity_service = EntityService(session)
        self._statement_service = StatementService(session)
        self._provenance = ProvenanceService(session)
        self._conflicts = ConflictService(session)

    def search_entities(self, request: SearchEntitiesRequest) -> SearchEntitiesResponse:
        timer = RequestTimer(operation="search_entities")
        patterns = lexical_match_patterns(request.query)
        token_match = or_(
            *(
                column.ilike(pattern, escape="\\")
                for pattern in patterns
                for column in (
                    Entity.canonical_name,
                    EntityAlias.alias,
                    EntityAlias.normalized_alias,
                )
            )
        )
        stmt = (
            select(Entity)
            .outerjoin(EntityAlias, EntityAlias.entity_id == Entity.id)
            .outerjoin(EntityType, EntityType.entity_id == Entity.id)
            .outerjoin(OntologyClass, OntologyClass.id == EntityType.class_id)
            .outerjoin(OntologyNamespace, OntologyNamespace.id == OntologyClass.namespace_id)
            .where(token_match)
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
        with timer.measure("db"):
            rows = list(self._session.scalars(stmt).all())
            alias_map = self._alias_map_for_entities([entity.id for entity in rows])
        now = request.as_of or datetime.now(UTC)
        hits: list[RankedEntityHit] = []
        with timer.measure("ranking"):
            for entity in rows:
                signals, reasons = self._score_entity(
                    entity,
                    query=request.query,
                    now=now,
                    aliases=alias_map.get(entity.id, ()),
                )
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
        response = SearchEntitiesResponse(
            query=request.query,
            hits=hits,
            ranking_explanations=[
                "Ranking combines tokenized lexical relevance, recency, and ontology specificity.",
                "Multi-term queries match any content token; exact names keep the strongest boost.",
                "ranking_score is for ordering only and is not a truth score.",
            ],
        )
        set_last_timings(timer.finish())
        return response

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
        temporal_intent = detect_temporal_intent(request.query)
        predicate_lexicon = (
            build_predicate_lexicon(self._session) if request.query else None
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
            patterns = lexical_match_patterns(request.query)
            subject_entity = aliased(Entity)
            object_entity = aliased(Entity)
            stmt = stmt.outerjoin(
                subject_entity, subject_entity.id == Statement.subject_entity_id
            ).outerjoin(
                object_entity, object_entity.id == Statement.object_entity_id
            )
            stmt = stmt.where(
                or_(
                    *(
                        column.ilike(pattern, escape="\\")
                        for pattern in patterns
                        for column in (
                            Statement.object_string,
                            Statement.normalized_object,
                            subject_entity.canonical_name,
                            object_entity.canonical_name,
                        )
                    )
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
        evidence_stats = self._provenance_repo.evidence_stats_for_statements(
            [row.id for row in rows]
        )
        name_map = self._entity_name_map_for_statements(rows)
        specificity_cache: dict[uuid.UUID, float] = {}
        hits: list[RankedStatementHit] = []
        for statement in rows:
            count, reliability = evidence_stats.get(statement.id, (0, 0.0))
            signals, reasons = self._score_statement(
                statement,
                query=request.query,
                focus_entity_id=focus,
                now=now,
                evidence_count=count,
                source_reliability=reliability,
                specificity_cache=specificity_cache,
                subject_name=name_map.get(statement.subject_entity_id),
                object_name=(
                    name_map.get(statement.object_entity_id)
                    if statement.object_entity_id is not None
                    else None
                ),
                temporal_intent=temporal_intent,
                predicate_lexicon=predicate_lexicon,
            )
            hits.append(
                RankedStatementHit(
                    statement=self._statement_service.to_response(statement),
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
                    "Query matching is tokenized and punctuation-insensitive across "
                    "object text and subject/object entity names."
                ),
                (
                    f"Temporal intent for ranking: {temporal_intent.value} "
                    "(current prefers effective open-ended facts; "
                    "historical promotes ended/superseded/legacy descriptive rows)."
                ),
                (
                    "Predicate intent is a soft ranking boost from ontology "
                    "labels/descriptions/aliases (never a hard filter)."
                ),
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
        timer = RequestTimer(operation="get_entity_neighborhood")
        with timer.measure("db_entity"):
            entity = self._entities.get(entity_id)
        if entity is None:
            raise UnknownEntityError(
                f"Entity {entity_id} was not found",
                details={"entity_id": str(entity_id)},
            )
        # Statement FKs are preserved on merge; reads follow the identity group.
        with timer.measure("db_identity"):
            identity_ids = set(self._entities.identity_group_ids(entity_id))
            survivor_id = self._entities.resolve_survivor_id(entity_id)
        with timer.measure("db_statements"):
            statements = self._statements.list_for_entity_timeline(list(identity_ids))
        now = as_of or datetime.now(UTC)
        edges: list[NeighborhoodEdge] = []
        with timer.measure("ranking_assemble"):
            candidates: list[tuple[Statement, str, uuid.UUID | None]] = []
            seen_statement_ids: set[uuid.UUID] = set()
            neighbor_ids: list[uuid.UUID] = []
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
                    neighbor_ids.append(neighbor)
                candidates.append((statement, direction, neighbor))

            survivor_map = self._entities.resolve_survivor_ids(neighbor_ids)
            evidence_stats = self._provenance_repo.evidence_stats_for_statements(
                [statement.id for statement, _, _ in candidates]
            )
            specificity_cache: dict[uuid.UUID, float] = {}
            scored: list[tuple[Statement, str, uuid.UUID | None, RankingSignals]] = []
            for statement, direction, neighbor in candidates:
                resolved_neighbor = (
                    survivor_map.get(neighbor, neighbor) if neighbor is not None else None
                )
                count, reliability = evidence_stats.get(statement.id, (0, 0.0))
                signals, _ = self._score_statement(
                    statement,
                    query=None,
                    focus_entity_id=survivor_id,
                    now=now,
                    evidence_count=count,
                    source_reliability=reliability,
                    specificity_cache=specificity_cache,
                )
                scored.append((statement, direction, resolved_neighbor, signals))
            scored.sort(key=lambda item: item[3].ranking_score, reverse=True)
            scored = scored[:limit]
            for statement, direction, neighbor, signals in scored:
                edges.append(
                    NeighborhoodEdge(
                        statement=self._statement_service.to_response(statement),
                        direction=direction,
                        neighbor_entity_id=neighbor,
                        signals=signals,
                        ranking_score=signals.ranking_score,
                    )
                )
        response = NeighborhoodResponse(
            entity_id=survivor_id if entity_id in identity_ids else entity_id,
            edges=edges,
            ranking_explanations=[
                "Neighborhood edges are ordered by temporal validity, recency, and evidence.",
                "Merged entity aliases are included; neighbor ids resolve to surviving entities.",
                "ranking_score is for ordering only and is not a truth score.",
            ],
        )
        set_last_timings(timer.finish())
        return response

    def search_semantic_memory(
        self, request: SearchSemanticMemoryRequest
    ) -> SearchSemanticMemoryResponse:
        temporal_intent = detect_temporal_intent(request.query)
        entity_hits = self.search_entities(
            SearchEntitiesRequest(
                query=request.query,
                class_key=request.class_key,
                namespace_key=request.namespace_key,
                as_of=request.as_of,
                limit=request.limit,
            )
        )
        # Always gather asserted lexical candidates. Historical intent widens the
        # asserted window and adds a superseded pass so prior beliefs are not
        # crowded out of search_statements' created_at pagination.
        statement_limit = (
            min(100, request.limit * 2)
            if temporal_intent == TemporalIntent.HISTORICAL
            else request.limit
        )
        statement_hits = list(
            self.search_statements(
                SearchStatementsRequest(
                    query=request.query,
                    entity_id=request.entity_id,
                    as_of=request.as_of,
                    limit=statement_limit,
                    status=StatementStatus.ASSERTED,
                )
            ).hits
        )
        if temporal_intent == TemporalIntent.HISTORICAL:
            superseded_hits = self.search_statements(
                SearchStatementsRequest(
                    query=request.query,
                    entity_id=request.entity_id,
                    as_of=request.as_of,
                    limit=statement_limit,
                    status=StatementStatus.SUPERSEDED,
                )
            ).hits
            seen = {hit.statement.id for hit in statement_hits}
            for hit in superseded_hits:
                if hit.statement.id not in seen:
                    statement_hits.append(hit)
                    seen.add(hit.statement.id)

        # Soft predicate-intent candidate pull: when the query clearly cues a
        # predicate (studied/goal/role), also fetch that predicate's rows so
        # entity-valued facts aren't lost to created_at pagination. Not a filter.
        predicate_lexicon = build_predicate_lexicon(self._session)
        intent_keys = top_intent_predicates(request.query, predicate_lexicon)
        seen_statement_ids = {hit.statement.id for hit in statement_hits}
        for pred_key in intent_keys:
            extra = self.search_statements(
                SearchStatementsRequest(
                    predicate_key=pred_key,
                    entity_id=request.entity_id,
                    as_of=request.as_of,
                    limit=min(15, request.limit),
                    status=(
                        None
                        if temporal_intent == TemporalIntent.HISTORICAL
                        else StatementStatus.ASSERTED
                    ),
                    query=None,
                )
            ).hits
            rescored = self._rescore_statements_for_query(
                [hit.statement.id for hit in extra],
                query=request.query,
                as_of=request.as_of,
                temporal_intent=temporal_intent,
                predicate_lexicon=predicate_lexicon,
            )
            for hit in rescored:
                if hit.statement.id in seen_statement_ids:
                    continue
                statement_hits.append(hit)
                seen_statement_ids.add(hit.statement.id)

        # Anchor expansion is historical-only: it exists to reach entity-valued
        # superseded facts with empty object_string. Running it on current/default
        # queries adds adjacency noise without a measured gain.
        anchor_count = 0
        if temporal_intent == TemporalIntent.HISTORICAL:
            anchor_hits, anchor_count = self._anchor_expand_statements(
                query=request.query,
                entity_hits=entity_hits.hits,
                focus_entity_id=request.entity_id,
                as_of=request.as_of,
                temporal_intent=temporal_intent,
                predicate_lexicon=predicate_lexicon,
            )
            for hit in anchor_hits:
                if hit.statement.id in seen_statement_ids:
                    existing_idx = next(
                        i
                        for i, row in enumerate(statement_hits)
                        if row.statement.id == hit.statement.id
                    )
                    existing = statement_hits[existing_idx]
                    if hit.ranking_score > existing.ranking_score:
                        merged_reasons = list(
                            dict.fromkeys([*hit.match_reasons, *existing.match_reasons])
                        )
                        statement_hits[existing_idx] = hit.model_copy(
                            update={"match_reasons": merged_reasons}
                        )
                    else:
                        merged_reasons = list(
                            dict.fromkeys([*existing.match_reasons, *hit.match_reasons])
                        )
                        statement_hits[existing_idx] = existing.model_copy(
                            update={"match_reasons": merged_reasons}
                        )
                else:
                    statement_hits.append(hit)
                    seen_statement_ids.add(hit.statement.id)

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
        for statement_hit in statement_hits:
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
                "Lexical matching is tokenized and punctuation-insensitive (no embeddings).",
                (
                    f"Temporal intent: {temporal_intent.value}. "
                    "Current/default search stays asserted-only and prefers effective facts; "
                    "historical intent includes superseded rows and boosts prior beliefs."
                ),
                (
                    "Bounded 1-hop anchor expansion runs only for historical intent "
                    f"(used {anchor_count} strong entity anchor(s); deduplicated)."
                    if temporal_intent == TemporalIntent.HISTORICAL
                    else "Bounded 1-hop anchor expansion skipped (non-historical intent)."
                ),
                (
                    "Predicate intent softly boosts ontology-matching predicates "
                    "(e.g. role→holdsRole); it never filters candidates out."
                ),
                "Vector search was not required and was not used.",
                "ranking_score is for ordering only and is not a truth score.",
            ],
            vector_search_used=False,
        )

    def get_relevant_context(self, request: RelevantContextRequest) -> RelevantContextResponse:
        timer = RequestTimer(operation="get_relevant_context")
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
            with timer.measure("statement_path"):
                explanation = self._provenance.explain_statement(request.statement_id)
                statement = self._statement_service.get(request.statement_id)
                subject_id = statement.subject_entity_id
                entity = self._entity_service.get(subject_id)
                timeline = self._statement_service.get_timeline(subject_id)
                neighborhood = self.get_entity_neighborhood(subject_id, as_of=request.as_of)
                conflicts = self._conflicts.find_conflicts(
                    statement_id=request.statement_id
                ).conflicts

        if request.entity_id is not None:
            with timer.measure("entity_get"):
                entity = self._entity_service.get(request.entity_id)
            with timer.measure("timeline"):
                timeline = self._statement_service.get_timeline(request.entity_id)
            with timer.measure("neighborhood"):
                neighborhood = self.get_entity_neighborhood(
                    request.entity_id, as_of=request.as_of
                )
            with timer.measure("conflicts"):
                conflicts = self._conflicts.find_conflicts(entity_id=request.entity_id).conflicts
            with timer.measure("statements"):
                statements = self.search_statements(
                    SearchStatementsRequest(
                        entity_id=request.entity_id,
                        as_of=request.as_of,
                        limit=request.limit,
                    )
                ).hits

        if request.query:
            with timer.measure("semantic_memory"):
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

        timings = timer.finish()
        set_last_timings(timings)
        return RelevantContextResponse(
            entity=entity,
            timeline=timeline,
            neighborhood=neighborhood,
            statements=statements[: request.limit],
            conflicts=conflicts,
            explanation=explanation,
            ranking_explanations=ranking_explanations,
            metadata={
                "vector_search_used": False,
                "llm_used": False,
                "timings_ms": timings.get("timings_ms"),
            },
        )

    def _rescore_statements_for_query(
        self,
        statement_ids: list[uuid.UUID],
        *,
        query: str,
        as_of: datetime | None,
        temporal_intent: TemporalIntent,
        predicate_lexicon: PredicateLexicon | None,
    ) -> list[RankedStatementHit]:
        """Score already-fetched statement ids with the user query (dedupe-safe)."""
        if not statement_ids:
            return []
        rows = list(
            self._session.scalars(
                select(Statement).where(Statement.id.in_(statement_ids))
            ).all()
        )
        if not rows:
            return []
        now = as_of or datetime.now(UTC)
        name_map = self._entity_name_map_for_statements(rows)
        evidence_stats = self._provenance_repo.evidence_stats_for_statements(
            [row.id for row in rows]
        )
        specificity_cache: dict[uuid.UUID, float] = {}
        hits: list[RankedStatementHit] = []
        for statement in rows:
            if statement.status == StatementStatus.RETRACTED.value:
                continue
            count, reliability = evidence_stats.get(statement.id, (0, 0.0))
            signals, reasons = self._score_statement(
                statement,
                query=query,
                focus_entity_id=None,
                now=now,
                evidence_count=count,
                source_reliability=reliability,
                specificity_cache=specificity_cache,
                subject_name=name_map.get(statement.subject_entity_id),
                object_name=(
                    name_map.get(statement.object_entity_id)
                    if statement.object_entity_id is not None
                    else None
                ),
                temporal_intent=temporal_intent,
                predicate_lexicon=predicate_lexicon,
            )
            reasons = list(dict.fromkeys([*reasons, "predicate_intent_candidate"]))
            hits.append(
                RankedStatementHit(
                    statement=self._statement_service.to_response(statement),
                    signals=signals,
                    ranking_score=signals.ranking_score,
                    match_reasons=reasons,
                )
            )
        return hits

    def _select_strong_anchors(
        self,
        entity_hits: list[RankedEntityHit],
        *,
        focus_entity_id: uuid.UUID | None,
    ) -> list[uuid.UUID]:
        """Pick a few strong lexical entity anchors for 1-hop expansion."""
        anchors: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        if focus_entity_id is not None:
            anchors.append(focus_entity_id)
            seen.add(focus_entity_id)
        for hit in entity_hits:
            if len(anchors) >= _MAX_ANCHOR_ENTITIES:
                break
            entity_id = hit.entity.id
            if entity_id in seen:
                continue
            strong_reason = any(
                reason in _STRONG_ANCHOR_REASONS for reason in hit.match_reasons
            )
            if hit.signals.lexical_relevance >= _MIN_ANCHOR_LEXICAL or strong_reason:
                anchors.append(entity_id)
                seen.add(entity_id)
        return anchors

    def _anchor_expand_statements(
        self,
        *,
        query: str,
        entity_hits: list[RankedEntityHit],
        focus_entity_id: uuid.UUID | None,
        as_of: datetime | None,
        temporal_intent: TemporalIntent,
        predicate_lexicon: PredicateLexicon | None = None,
    ) -> tuple[list[RankedStatementHit], int]:
        """Expand 1-hop statements around strong entity anchors.

        Reuses the normal statement scorer. Asserted-only by default; historical
        intent also admits superseded rows. Retracted rows are never expanded.
        """
        anchors = self._select_strong_anchors(
            entity_hits, focus_entity_id=focus_entity_id
        )
        if not anchors:
            return [], 0

        now = as_of or datetime.now(UTC)
        allowed_statuses = {StatementStatus.ASSERTED.value}
        if temporal_intent == TemporalIntent.HISTORICAL:
            allowed_statuses.add(StatementStatus.SUPERSEDED.value)

        collected: list[Statement] = []
        seen_ids: set[uuid.UUID] = set()
        for anchor_id in anchors:
            identity_ids = self._entities.identity_group_ids(anchor_id)
            rows = self._statements.list_for_entity_timeline(identity_ids)
            per_anchor = 0
            for statement in rows:
                if statement.status not in allowed_statuses:
                    continue
                # Target the structural blind spot: entity-valued statements with
                # little/no object text. Free-text rows are already covered lexically.
                if statement.object_entity_id is None:
                    continue
                if statement.id in seen_ids:
                    continue
                collected.append(statement)
                seen_ids.add(statement.id)
                per_anchor += 1
                if per_anchor >= _MAX_STATEMENTS_PER_ANCHOR:
                    break
            if len(collected) >= _MAX_ANCHOR_STATEMENTS_TOTAL:
                collected = collected[:_MAX_ANCHOR_STATEMENTS_TOTAL]
                break

        if not collected:
            return [], len(anchors)

        name_map = self._entity_name_map_for_statements(collected)
        evidence_stats = self._provenance_repo.evidence_stats_for_statements(
            [row.id for row in collected]
        )
        specificity_cache: dict[uuid.UUID, float] = {}
        # Map statement → best focus among anchors for proximity scoring.
        anchor_set = set(anchors)
        for anchor_id in list(anchors):
            anchor_set.update(self._entities.identity_group_ids(anchor_id))

        hits: list[RankedStatementHit] = []
        for statement in collected:
            focus = None
            if statement.subject_entity_id in anchor_set:
                focus = statement.subject_entity_id
            elif (
                statement.object_entity_id is not None
                and statement.object_entity_id in anchor_set
            ):
                focus = statement.object_entity_id
            count, reliability = evidence_stats.get(statement.id, (0, 0.0))
            signals, reasons = self._score_statement(
                statement,
                query=query,
                focus_entity_id=focus,
                now=now,
                evidence_count=count,
                source_reliability=reliability,
                specificity_cache=specificity_cache,
                subject_name=name_map.get(statement.subject_entity_id),
                object_name=(
                    name_map.get(statement.object_entity_id)
                    if statement.object_entity_id is not None
                    else None
                ),
                temporal_intent=temporal_intent,
                from_anchor=True,
                predicate_lexicon=predicate_lexicon,
            )
            hits.append(
                RankedStatementHit(
                    statement=self._statement_service.to_response(statement),
                    signals=signals,
                    ranking_score=signals.ranking_score,
                    match_reasons=reasons,
                )
            )
        return hits, len(anchors)

    def _alias_map_for_entities(
        self, entity_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[str, ...]]:
        if not entity_ids:
            return {}
        rows = self._session.execute(
            select(EntityAlias.entity_id, EntityAlias.alias, EntityAlias.normalized_alias).where(
                EntityAlias.entity_id.in_(entity_ids)
            )
        ).all()
        out: dict[uuid.UUID, list[str]] = {entity_id: [] for entity_id in entity_ids}
        for entity_id, alias, normalized in rows:
            bucket = out.setdefault(entity_id, [])
            if alias:
                bucket.append(alias)
            if normalized:
                bucket.append(normalized)
        return {key: tuple(values) for key, values in out.items()}

    def _entity_name_map_for_statements(
        self, statements: list[Statement]
    ) -> dict[uuid.UUID, str]:
        ids: set[uuid.UUID] = set()
        for statement in statements:
            ids.add(statement.subject_entity_id)
            if statement.object_entity_id is not None:
                ids.add(statement.object_entity_id)
        if not ids:
            return {}
        rows = self._session.execute(
            select(Entity.id, Entity.canonical_name).where(Entity.id.in_(ids))
        ).all()
        return {entity_id: name for entity_id, name in rows}

    def _score_entity(
        self,
        entity: Entity,
        *,
        query: str,
        now: datetime,
        aliases: tuple[str, ...] = (),
    ) -> tuple[RankingSignals, list[str]]:
        lexical, reasons, notes = score_lexical_relevance(
            query,
            [entity.canonical_name, *aliases],
        )
        if not reasons:
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
        evidence_count: int | None = None,
        source_reliability: float | None = None,
        specificity_cache: dict[uuid.UUID, float] | None = None,
        subject_name: str | None = None,
        object_name: str | None = None,
        temporal_intent: TemporalIntent | None = None,
        from_anchor: bool = False,
        predicate_lexicon: PredicateLexicon | None = None,
    ) -> tuple[RankingSignals, list[str]]:
        notes: list[str] = []
        reasons: list[str] = []
        intent = temporal_intent or (
            detect_temporal_intent(query) if query else TemporalIntent.NEUTRAL
        )

        lexical = 0.0
        if query:
            haystacks: list[str | None] = [
                statement.object_string,
                statement.normalized_object,
            ]
            if from_anchor and focus_entity_id is not None:
                # Do not count the anchor's own name as lexical content — that
                # would make every Didac-adjacent statement look like an exact hit.
                if statement.subject_entity_id != focus_entity_id:
                    haystacks.append(subject_name)
                if statement.object_entity_id != focus_entity_id:
                    haystacks.append(object_name)
            else:
                haystacks.extend([subject_name, object_name])
            lexical, lex_reasons, lex_notes = score_lexical_relevance(query, haystacks)
            reasons.extend(lex_reasons)
            notes.extend(lex_notes)
            if not lex_reasons:
                lexical = 0.2
                reasons.append("structural_match")
        else:
            lexical = 0.4
            reasons.append("entity_linked_statement")

        if from_anchor:
            reasons.append("anchor_1hop")
            notes.append(
                "Candidate via bounded 1-hop expansion from a strong entity anchor "
                "(entity-valued statements only)."
            )
            # Empty object_string entity links need a floor to compete.
            # Superseded historical facts get a higher floor so they outrank
            # unbounded adjacency noise (hasParticipant, etc.).
            floor = _ANCHOR_LEXICAL_FLOOR
            if (
                intent == TemporalIntent.HISTORICAL
                and statement.status == StatementStatus.SUPERSEDED.value
            ):
                # Keep the high superseded floor only when the query's predicate
                # intent agrees (or is empty). Otherwise study/goal cues lose to
                # every superseded neighbor.
                apply_high_floor = True
                if query and predicate_lexicon is not None:
                    intent_keys = top_intent_predicates(query, predicate_lexicon, limit=2)
                    my_key = predicate_lexicon.key_for(statement.predicate_id)
                    if intent_keys and my_key not in intent_keys:
                        apply_high_floor = False
                if apply_high_floor:
                    floor = max(floor, 0.92)
                    reasons.append("anchor_historical_superseded_boost")
            if lexical < floor:
                lexical = floor
                notes.append(
                    f"Applied anchor lexical floor {floor:.2f} "
                    "for entity-valued / low-text statements."
                )

        if query and predicate_lexicon is not None:
            predicate_key = predicate_lexicon.key_for(statement.predicate_id)
            intent_boost, intent_reasons, intent_notes = score_predicate_intent(
                query, predicate_key, predicate_lexicon
            )
            if intent_boost > 0:
                lexical = min(1.0, lexical + 0.22 * intent_boost)
                reasons.extend(intent_reasons)
                notes.extend(intent_notes)

        temporal, temporal_reasons, temporal_notes = score_temporal_validity(
            valid_from=statement.valid_from,
            valid_to=statement.valid_to,
            status=statement.status,
            now=now,
            intent=intent,
        )
        reasons.extend(temporal_reasons)
        notes.extend(temporal_notes)

        # Historical intent: denser free-text descriptions that answer
        # "what did we used to believe" should outrank sparse name-only joins.
        if (
            intent == TemporalIntent.HISTORICAL
            and statement.object_string
            and lexical >= 0.45
        ):
            lexical = min(1.0, lexical + 0.14)
            reasons.append("historical_description_boost")
            notes.append(
                "Historical intent boosts substantive object-text matches "
                "(legacy descriptive duplicates)."
            )
            query_tokens = lexical_tokens(query) if query else []
            blob = statement.object_string.casefold()
            if "start" in query_tokens and "started" in blob:
                lexical = min(1.0, lexical + 0.12)
                reasons.append("historical_start_belief_boost")
                notes.append(
                    "Historical start-date questions boost descriptions that "
                    "explicitly say when something started."
                )

        age_seconds = max(0.0, (now - statement.asserted_at).total_seconds())
        recency = _clamp(1.0 - (age_seconds / (86400.0 * 365.0)))
        notes.append("Recency decays over about one year from asserted_at.")

        if evidence_count is None or source_reliability is None:
            stats = self._provenance_repo.evidence_stats_for_statements([statement.id])
            count, reliability = stats.get(statement.id, (0, 0.0))
            if evidence_count is None:
                evidence_count = count
            if source_reliability is None:
                source_reliability = reliability
        evidence_presence = 1.0 if evidence_count > 0 else 0.0
        if evidence_presence:
            reasons.append("has_evidence")
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

        specificity = self._ontology_specificity(
            statement.predicate_id, cache=specificity_cache
        )
        notes.append("Ontology specificity rises with domain/range constraints.")

        signals = RankingSignals(
            lexical_relevance=lexical,
            temporal_validity=temporal,
            recency=recency,
            evidence_presence=evidence_presence,
            source_reliability=source_reliability,
            entity_proximity=proximity,
            ontology_specificity=specificity,
            notes=notes,
        )
        return signals, reasons

    def _ontology_specificity(
        self,
        predicate_id: uuid.UUID,
        *,
        cache: dict[uuid.UUID, float] | None = None,
    ) -> float:
        if cache is not None and predicate_id in cache:
            return cache[predicate_id]
        predicate = self._session.get(OntologyPredicate, predicate_id)
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
        if cache is not None:
            cache[predicate_id] = specificity
        return specificity

    def _temporal_validity(
        self,
        statement: Statement,
        now: datetime,
        *,
        intent: TemporalIntent = TemporalIntent.NEUTRAL,
    ) -> float:
        score, _, _ = score_temporal_validity(
            valid_from=statement.valid_from,
            valid_to=statement.valid_to,
            status=statement.status,
            now=now,
            intent=intent,
        )
        return score
