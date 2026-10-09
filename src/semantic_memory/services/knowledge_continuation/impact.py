"""Downstream impact ranking over the candidate dependency graph."""

from __future__ import annotations

import uuid
from collections import defaultdict, deque

from semantic_memory.models.enums import KnowledgeCandidateState
from semantic_memory.models.knowledge_ingestion import (
    KnowledgeCandidate,
    KnowledgeCandidateDependency,
)

_STOP_TRAVERSAL = frozenset(
    {
        KnowledgeCandidateState.COMMITTED.value,
        KnowledgeCandidateState.DISCARDED.value,
        KnowledgeCandidateState.FAILED.value,
    }
)


def build_children_map(
    deps: list[KnowledgeCandidateDependency],
) -> dict[uuid.UUID, list[uuid.UUID]]:
    children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for edge in deps:
        children[edge.parent_candidate_id].append(edge.child_candidate_id)
    return children


def transitive_blocked_set(
    roots: set[uuid.UUID],
    *,
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]],
    candidates_by_id: dict[uuid.UUID, KnowledgeCandidate],
) -> set[uuid.UUID]:
    """Union of blocked candidates reachable downstream from any root (roots included)."""
    out: set[uuid.UUID] = set()
    seen: set[uuid.UUID] = set()
    queue: deque[uuid.UUID] = deque(roots)
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        cand = candidates_by_id.get(node)
        if cand is None:
            continue
        if cand.state == KnowledgeCandidateState.BLOCKED.value:
            out.add(node)
        if cand.state in _STOP_TRAVERSAL:
            continue
        queue.extend(children_by_parent.get(node, []))
    return out


def impact_blocked_count(
    roots: set[uuid.UUID],
    *,
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]],
    candidates_by_id: dict[uuid.UUID, KnowledgeCandidate],
) -> int:
    return len(
        transitive_blocked_set(
            roots,
            children_by_parent=children_by_parent,
            candidates_by_id=candidates_by_id,
        )
    )
