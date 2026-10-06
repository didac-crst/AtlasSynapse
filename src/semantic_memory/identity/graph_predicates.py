"""Allowlisted predicates used for identity graph-context evidence (PR4)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GraphEdgeDirection(StrEnum):
    """How to read subject/object for an identity-relevant edge."""

    OUTGOING = "outgoing"
    SYMMETRIC = "symmetric"


class GraphCompareRole(StrEnum):
    """Semantic role for conservative comparison (never decisive alone)."""

    EMPLOYER = "employer"
    SPOUSE = "spouse"
    CHILD = "child"
    ADDRESS = "address"
    RELATED = "related"


@dataclass(frozen=True, slots=True)
class IdentityGraphPredicateSpec:
    predicate_key: str
    role: GraphCompareRole
    direction: GraphEdgeDirection = GraphEdgeDirection.OUTGOING


# Small allowlist: only predicates with defensible identity semantics in PR4.
IDENTITY_GRAPH_PREDICATE_SPECS: dict[str, IdentityGraphPredicateSpec] = {
    "employedBy": IdentityGraphPredicateSpec(
        predicate_key="employedBy",
        role=GraphCompareRole.EMPLOYER,
        direction=GraphEdgeDirection.OUTGOING,
    ),
    "spouseOf": IdentityGraphPredicateSpec(
        predicate_key="spouseOf",
        role=GraphCompareRole.SPOUSE,
        direction=GraphEdgeDirection.SYMMETRIC,
    ),
    "parentOf": IdentityGraphPredicateSpec(
        predicate_key="parentOf",
        role=GraphCompareRole.CHILD,
        direction=GraphEdgeDirection.OUTGOING,
    ),
    "locatedAt": IdentityGraphPredicateSpec(
        predicate_key="locatedAt",
        role=GraphCompareRole.ADDRESS,
        direction=GraphEdgeDirection.OUTGOING,
    ),
}

IDENTITY_GRAPH_PREDICATE_KEYS: frozenset[str] = frozenset(IDENTITY_GRAPH_PREDICATE_SPECS.keys())
