"""Ontology read-plane schemas."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from semantic_memory.models.enums import AliasTargetType, Cardinality, ValueKind


class OntologyClassRef(BaseModel):
    id: uuid.UUID
    namespace_key: str
    key: str
    label: str | None = None
    description: str | None = None
    is_deprecated: bool = False


class OntologyPredicateResponse(BaseModel):
    id: uuid.UUID
    namespace_key: str
    key: str
    label: str | None = None
    description: str | None = None
    value_kind: ValueKind | None = None
    datatype: str | None = None
    cardinality: Cardinality | None = None
    is_symmetric: bool = False
    is_transitive: bool = False
    domain_class_keys: list[str] = Field(default_factory=list)
    range_class_keys: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    is_deprecated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class OntologyClassResponse(BaseModel):
    id: uuid.UUID
    namespace_key: str
    key: str
    label: str | None = None
    description: str | None = None
    parent_class_keys: list[str] = Field(default_factory=list)
    ancestor_class_keys: list[str] = Field(default_factory=list)
    descendant_class_keys: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    is_deprecated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class OntologyHitType(StrEnum):
    CLASS = "class"
    PREDICATE = "predicate"
    ALIAS = "alias"


class OntologySearchHit(BaseModel):
    hit_type: OntologyHitType
    namespace_key: str
    key: str
    label: str | None = None
    alias: str | None = None
    target_type: AliasTargetType | None = None


class OntologySearchResponse(BaseModel):
    query: str
    hits: list[OntologySearchHit] = Field(default_factory=list)


class OntologyContextResponse(BaseModel):
    ontology_class: OntologyClassResponse
    applicable_predicates: list[OntologyPredicateResponse] = Field(default_factory=list)
