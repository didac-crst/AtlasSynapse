"""Commit-path routing: Claim vs domain assertion."""

from __future__ import annotations

from semantic_memory.models.enums import (
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
)
from semantic_memory.models.knowledge_ingestion import KnowledgeCandidate
from semantic_memory.services.knowledge_resolution.schemas import CommitPath


def route_commit_path(candidate: KnowledgeCandidate) -> CommitPath:
    kind = candidate.kind
    polarity = candidate.polarity

    if kind in {
        KnowledgeCandidateKind.HYPOTHESIS.value,
        KnowledgeCandidateKind.RECOMMENDATION.value,
        KnowledgeCandidateKind.QUESTION.value,
    }:
        return CommitPath.CLAIM

    if kind == KnowledgeCandidateKind.ASSERTION.value:
        if polarity == KnowledgeCandidatePolarity.NEGATIVE.value:
            return CommitPath.CLAIM
        if looks_domain_representable(candidate):
            return CommitPath.DOMAIN_ASSERTION
        return CommitPath.CLAIM

    return CommitPath.CLAIM


def claim_epistemic_kind(candidate: KnowledgeCandidate) -> str:
    """epistemicKind for Claim commits."""
    if candidate.kind == KnowledgeCandidateKind.ASSERTION.value:
        return "assertion"
    return candidate.kind


def looks_domain_representable(candidate: KnowledgeCandidate) -> bool:
    """Positive assertions need SPO-like structure to attempt the domain path."""
    payload = candidate.claim_payload if isinstance(candidate.claim_payload, dict) else {}
    subject = payload.get("subject")
    predicate = payload.get("predicate_key_hint") or payload.get("predicate_text")
    obj = payload.get("object")
    has_subject = isinstance(subject, dict) and bool(subject.get("text"))
    has_pred = isinstance(predicate, str) and bool(predicate.strip())
    has_object = (isinstance(obj, dict) and bool(obj.get("text"))) or obj is not None
    # Domain path only when structured enough; otherwise Claim fallback.
    return bool(has_subject and has_pred and has_object and candidate.claim_text)
