"""Soft predicate intent for retrieval ranking.

Derives candidate cues primarily from ontology predicate keys (camelCase parts),
labels, descriptions, and aliases. A tiny NL overlay covers words that agents
actually say (job/position/study) which are weak or absent in bootstrap labels.

Shared cues like ``role`` (holdsRole + roleAt) only count when distinctive or
paired with an NL cue — avoiding over-boost of every role-adjacent predicate.

This is a **soft ranking boost / candidate hint only** — never a hard filter.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    OntologyAlias,
    OntologyNamespace,
    OntologyPredicate,
    OntologyPredicateRevision,
)
from semantic_memory.services.lexical import lexical_tokens

_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

# Predicates that are too generic or temporal-boundary to receive intent boosts.
_NO_INTENT_BOOST = frozenset(
    {
        "startedAt",
        "endedAt",
        "occurredAt",
        "targetDate",
        "actor",
        "source",
        "name",
        "description",
        "relatedTo",
        "hasParticipant",
        "dependsOn",
    }
)

# Minimal NL overlay — prefer specific agent phrasing over shared ontology stems.
_NL_CUES: dict[str, frozenset[str]] = {
    "job": frozenset({"holdsRole", "employedBy"}),
    "jobs": frozenset({"holdsRole", "employedBy"}),
    "position": frozenset({"holdsRole"}),
    "positions": frozenset({"holdsRole"}),
    "title": frozenset({"holdsRole"}),
    "employer": frozenset({"employedBy"}),
    "employers": frozenset({"employedBy"}),
    "work": frozenset({"employedBy"}),
    "works": frozenset({"employedBy"}),
    "school": frozenset({"studiedAt"}),
    "university": frozenset({"studiedAt"}),
    "college": frozenset({"studiedAt"}),
    "plan": frozenset({"hasGoal"}),
    "plans": frozenset({"hasGoal"}),
    "goal": frozenset({"hasGoal"}),
    "goals": frozenset({"hasGoal"}),
    "objective": frozenset({"hasGoal"}),
    "objectives": frozenset({"hasGoal"}),
    "study": frozenset({"studiedAt"}),
    "studies": frozenset({"studiedAt"}),
    "studied": frozenset({"studiedAt"}),
    # Prefer holdsRole for bare "role"; roleAt still matches via ontology when
    # combined with org/context cues ("role at").
    "role": frozenset({"holdsRole"}),
    "roles": frozenset({"holdsRole"}),
    "wife": frozenset({"spouseOf"}),
    "husband": frozenset({"spouseOf"}),
    "spouse": frozenset({"spouseOf"}),
    "child": frozenset({"parentOf"}),
    "children": frozenset({"parentOf"}),
    "daughter": frozenset({"parentOf"}),
    "daughters": frozenset({"parentOf"}),
    "son": frozenset({"parentOf"}),
    "sons": frozenset({"parentOf"}),
}


def _camel_parts(value: str) -> set[str]:
    return {part.casefold() for part in _CAMEL_RE.findall(value) if len(part) >= 2}


def _with_simple_plurals(tokens: set[str]) -> set[str]:
    out = set(tokens)
    for token in tokens:
        if token.endswith("s") and len(token) > 3:
            out.add(token[:-1])
        else:
            out.add(f"{token}s")
    return out


@dataclass(frozen=True)
class PredicateLexicon:
    """predicate_key → searchable cue tokens from ontology."""

    tokens_by_key: dict[str, frozenset[str]]
    key_by_id: dict[uuid.UUID, str]
    distinctive_tokens: frozenset[str]

    def key_for(self, predicate_id: uuid.UUID) -> str | None:
        return self.key_by_id.get(predicate_id)


def build_predicate_lexicon(session: Session, *, namespace_key: str = "core") -> PredicateLexicon:
    """Load active predicates and derive cue tokens from ontology text."""
    rows = session.execute(
        select(
            OntologyPredicate.id,
            OntologyPredicate.key,
            OntologyPredicateRevision.label,
            OntologyPredicateRevision.description,
        )
        .join(
            OntologyNamespace,
            OntologyNamespace.id == OntologyPredicate.namespace_id,
        )
        .join(
            OntologyPredicateRevision,
            OntologyPredicateRevision.id == OntologyPredicate.current_revision_id,
        )
        .where(
            OntologyNamespace.key == namespace_key,
            OntologyPredicate.is_deprecated.is_(False),
        )
    ).all()

    tokens_by_key: dict[str, set[str]] = {}
    key_by_id: dict[uuid.UUID, str] = {}
    predicate_ids: list[uuid.UUID] = []
    for predicate_id, key, label, description in rows:
        key_by_id[predicate_id] = key
        predicate_ids.append(predicate_id)
        cues = set(_camel_parts(key))
        if label:
            cues |= set(lexical_tokens(label))
            cues |= _camel_parts(label)
        if description:
            cues |= set(lexical_tokens(description))
        tokens_by_key[key] = cues

    if predicate_ids:
        alias_rows = session.execute(
            select(OntologyAlias.predicate_id, OntologyAlias.alias).where(
                OntologyAlias.predicate_id.in_(predicate_ids)
            )
        ).all()
        for predicate_id, alias in alias_rows:
            key = key_by_id.get(predicate_id)
            if key is None or not alias:
                continue
            tokens_by_key.setdefault(key, set()).update(lexical_tokens(alias))
            tokens_by_key[key].update(_camel_parts(alias))

    counts: Counter[str] = Counter()
    for cues in tokens_by_key.values():
        for cue in _with_simple_plurals(set(cues)):
            counts[cue] += 1
    # Tokens unique to a single predicate are safe ontology boosts.
    # Shared stems like ``role`` (holdsRole + roleAt) require an NL cue instead.
    distinctive = frozenset(token for token, n in counts.items() if n <= 1)

    return PredicateLexicon(
        tokens_by_key={key: frozenset(cues) for key, cues in tokens_by_key.items()},
        key_by_id=key_by_id,
        distinctive_tokens=distinctive,
    )


def score_predicate_intent(
    query: str | None,
    predicate_key: str | None,
    lexicon: PredicateLexicon | None,
) -> tuple[float, list[str], list[str]]:
    """Return (boost 0..1, reasons, notes). Soft signal only."""
    reasons: list[str] = []
    notes: list[str] = []
    if not query or not predicate_key or lexicon is None:
        return 0.0, reasons, notes
    if predicate_key in _NO_INTENT_BOOST:
        return 0.0, reasons, notes

    q_tokens = set(lexical_tokens(query))
    raw = {tok for tok in re.findall(r"[a-z0-9]+", query.casefold()) if len(tok) >= 2}
    q_tokens |= raw & set(_NL_CUES)
    q_match = _with_simple_plurals(q_tokens)
    if not q_tokens:
        return 0.0, reasons, notes

    ontology_cues = _with_simple_plurals(set(lexicon.tokens_by_key.get(predicate_key, ())))
    overlap = q_match & ontology_cues
    distinctive_overlap = overlap & lexicon.distinctive_tokens

    nl_hit = False
    for token in q_match:
        targets = _NL_CUES.get(token)
        if targets and predicate_key in targets:
            nl_hit = True
            break

    score = 0.0
    if distinctive_overlap:
        score = min(1.0, 0.60 + 0.20 * min(3, len(distinctive_overlap)))
        reasons.append("predicate_intent_ontology")
        notes.append(
            f"Distinctive ontology cues {sorted(distinctive_overlap)[:6]} for {predicate_key}."
        )
    elif len(overlap) >= 2:
        score = min(1.0, 0.50 + 0.15 * min(3, len(overlap)))
        reasons.append("predicate_intent_ontology")
        notes.append(f"Multi-token ontology cues {sorted(overlap)[:6]} for {predicate_key}.")
    # Shared single-token ontology stems (e.g. bare "role") do not boost alone.

    if nl_hit:
        score = max(score, 0.78)
        reasons.append("predicate_intent_nl_cue")
        notes.append(f"NL cue soft-boosts predicate {predicate_key}.")

    return score, reasons, notes


def top_intent_predicates(
    query: str,
    lexicon: PredicateLexicon,
    *,
    limit: int = 2,
    min_score: float = 0.55,
) -> list[str]:
    """Highest-scoring predicate keys for optional candidate expansion."""
    scored: list[tuple[float, str]] = []
    for key in lexicon.tokens_by_key:
        boost, _, _ = score_predicate_intent(query, key, lexicon)
        if boost >= min_score:
            scored.append((boost, key))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [key for _, key in scored[:limit]]
