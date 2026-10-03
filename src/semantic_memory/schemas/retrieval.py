"""Retrieval request/response schemas with transparent ranking signals."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from semantic_memory.models.enums import EntityStatus, StatementStatus
from semantic_memory.schemas.conflicts import ConflictResponse
from semantic_memory.schemas.entities import EntityResponse
from semantic_memory.schemas.provenance import ExplainStatementResponse
from semantic_memory.schemas.statements import StatementResponse, TimelineResponse


class RankingSignals(BaseModel):
    """Transparent ranking inputs. Not a truth score."""

    lexical_relevance: float = 0.0
    temporal_validity: float = 0.0
    recency: float = 0.0
    evidence_presence: float = 0.0
    source_reliability: float = 0.0
    entity_proximity: float = 0.0
    ontology_specificity: float = 0.0
    notes: list[str] = Field(default_factory=list)

    @property
    def ranking_score(self) -> float:
        """Ordering aid only — never authoritative for truth or identity."""
        return (
            self.lexical_relevance * 0.30
            + self.temporal_validity * 0.15
            + self.recency * 0.15
            + self.evidence_presence * 0.10
            + self.source_reliability * 0.10
            + self.entity_proximity * 0.10
            + self.ontology_specificity * 0.10
        )


class RankedEntityHit(BaseModel):
    entity: EntityResponse
    signals: RankingSignals
    ranking_score: float
    match_reasons: list[str] = Field(default_factory=list)


class RankedStatementHit(BaseModel):
    statement: StatementResponse
    signals: RankingSignals
    ranking_score: float
    match_reasons: list[str] = Field(default_factory=list)


class SearchEntitiesRequest(BaseModel):
    query: str = Field(min_length=1)
    class_key: str | None = None
    namespace_key: str = "core"
    status: EntityStatus | None = EntityStatus.ACTIVE
    limit: int = Field(default=25, ge=1, le=100)
    as_of: datetime | None = None


class SearchEntitiesResponse(BaseModel):
    query: str
    hits: list[RankedEntityHit] = Field(default_factory=list)
    ranking_explanations: list[str] = Field(default_factory=list)


class SearchStatementsRequest(BaseModel):
    query: str | None = None
    entity_id: uuid.UUID | None = None
    predicate_key: str | None = None
    namespace_key: str = "core"
    status: StatementStatus | None = StatementStatus.ASSERTED
    as_of: datetime | None = None
    limit: int = Field(default=25, ge=1, le=100)


class SearchStatementsResponse(BaseModel):
    query: str | None = None
    hits: list[RankedStatementHit] = Field(default_factory=list)
    ranking_explanations: list[str] = Field(default_factory=list)


class NeighborhoodEdge(BaseModel):
    statement: StatementResponse
    direction: str
    neighbor_entity_id: uuid.UUID | None = None
    signals: RankingSignals
    ranking_score: float


class NeighborhoodResponse(BaseModel):
    entity_id: uuid.UUID
    edges: list[NeighborhoodEdge] = Field(default_factory=list)
    ranking_explanations: list[str] = Field(default_factory=list)


class MemoryHitType(StrEnum):
    ENTITY = "entity"
    STATEMENT = "statement"
    CONFLICT = "conflict"


class SemanticMemoryHit(BaseModel):
    hit_type: MemoryHitType
    entity: EntityResponse | None = None
    statement: StatementResponse | None = None
    conflict: ConflictResponse | None = None
    signals: RankingSignals
    ranking_score: float
    match_reasons: list[str] = Field(default_factory=list)


class SearchSemanticMemoryRequest(BaseModel):
    query: str = Field(min_length=1)
    entity_id: uuid.UUID | None = None
    class_key: str | None = None
    namespace_key: str = "core"
    as_of: datetime | None = None
    include_conflicts: bool = True
    limit: int = Field(default=25, ge=1, le=100)


class SearchSemanticMemoryResponse(BaseModel):
    query: str
    hits: list[SemanticMemoryHit] = Field(default_factory=list)
    ranking_explanations: list[str] = Field(default_factory=list)
    vector_search_used: bool = False


class RelevantContextRequest(BaseModel):
    query: str | None = None
    entity_id: uuid.UUID | None = None
    statement_id: uuid.UUID | None = None
    namespace_key: str = "core"
    as_of: datetime | None = None
    limit: int = Field(default=25, ge=1, le=100)


class RelevantContextResponse(BaseModel):
    entity: EntityResponse | None = None
    timeline: TimelineResponse | None = None
    neighborhood: NeighborhoodResponse | None = None
    statements: list[RankedStatementHit] = Field(default_factory=list)
    conflicts: list[ConflictResponse] = Field(default_factory=list)
    explanation: ExplainStatementResponse | None = None
    ranking_explanations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
