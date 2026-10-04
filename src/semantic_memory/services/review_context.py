"""Minimized ontology context builder for semantic review."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import IntEnum
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.models.enums import ProposalType
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.validation.normalization import normalize_text

CONTEXT_BUILDER_VERSION = "semantic-context-v4.1"

# Always-considered peers for Thing-rooted proposals so near-duplicates like Role /
# dependsOn are not starved by alphabetical generic siblings when lexical signal is thin.
_HIGH_VALUE_CLASS_KEYS = frozenset(
    {
        "Role",
        "Goal",
        "Preference",
        "Skill",
        "System",
        "Agent",
        "Organization",
        "Person",
        "RelationshipContext",
        "Employment",
        "Constraint",
        "Event",
        "Decision",
        "Project",
    }
)
_HIGH_VALUE_PREDICATE_KEYS = frozenset(
    {
        "dependsOn",
        "relatedTo",
        "holdsRole",
        "roleAt",
        "deployedOn",
        "hasGoal",
        "hasSkill",
        "hasPreference",
    }
)
_HIGH_VALUE_PEER_FLOOR = 0.36


@dataclass(frozen=True)
class DerivationHintMatch:
    """Compact policy-relevant derivation / modeling hints for the reviewer."""

    hints: tuple[str, ...]
    pin_concepts: tuple[tuple[str, str], ...] = ()
    # When True, AtlasSynapse should not accept assertive approve/reject/reuse
    # on the first pass — ask the proposer whether composition already suffices.
    prefer_clarification: bool = False


class ContextTier(IntEnum):
    """Lower value = higher retention priority when truncating."""

    PINNED = 0
    SUPPORTING = 1
    GENERIC = 2


@dataclass(frozen=True)
class ContextConcept:
    kind: str
    key: str
    label: str | None = None
    description: str | None = None
    score: float = 0.0
    reason: str = ""
    tier: ContextTier = ContextTier.SUPPORTING
    selection_trace: str = ""

    def identity(self) -> tuple[str, str]:
        return self.kind, self.key


@dataclass
class ReviewContext:
    proposal_type: ProposalType
    proposal: dict[str, Any]
    concepts: list[ContextConcept] = field(default_factory=list)
    derivation_hints: list[str] = field(default_factory=list)
    prefer_clarification: bool = False
    estimated_tokens: int = 0
    budget_exceeded: bool = False
    builder_version: str = CONTEXT_BUILDER_VERSION
    input_hash: str = ""
    candidate_selection_trace: list[dict[str, Any]] = field(default_factory=list)

    def concept_keys(self) -> list[str]:
        return [f"{item.kind}:{item.key}" for item in self.concepts]

    def to_prompt_block(self) -> str:
        lines = ["RELEVANT EXISTING ONTOLOGY:"]
        if not self.concepts:
            lines.append("(no close ontology candidates)")
        else:
            for item in self.concepts:
                desc = (item.description or "").strip()
                if len(desc) > 180:
                    desc = desc[:177] + "..."
                lines.append(
                    f"- {item.kind}:{item.key}"
                    f" label={item.label or item.key}"
                    f" reason={item.reason}"
                    f" score={item.score:.3f}"
                    f" tier={item.tier.name.lower()}"
                    + (f" desc={desc}" if desc else "")
                )
        if self.derivation_hints:
            lines.append("")
            lines.append("CANONICAL DERIVATION HINTS:")
            for hint in self.derivation_hints:
                lines.append(f"- {hint}")
        return "\n".join(lines)


class SemanticReviewContextBuilder:
    """Build bounded ontology-only context for LLM semantic review."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._ontology = OntologyRepository(session)
        self._session = session
        self._settings = settings or get_settings()

    def build(
        self,
        *,
        proposal_type: ProposalType,
        payload: dict[str, Any],
        embedding_candidates: list[dict[str, Any]] | None = None,
    ) -> ReviewContext:
        max_candidates = self._settings.semantic_review_max_candidates
        max_tokens = self._settings.semantic_review_max_context_tokens
        hint_match = match_derivation_hints(proposal_type=proposal_type, payload=payload)
        ranked = self._retrieve_candidates(
            proposal_type=proposal_type,
            payload=payload,
            embedding_candidates=embedding_candidates or [],
        )
        if hint_match is not None:
            ranked = self._merge_derivation_pins(
                namespace_key=str(payload.get("namespace_key") or "core"),
                ranked=ranked,
                pin_concepts=hint_match.pin_concepts,
            )
        derivation_hints = list(hint_match.hints) if hint_match is not None else []
        prefer_clarification = bool(
            hint_match.prefer_clarification if hint_match is not None else False
        )
        packed = self._pack_candidates(
            proposal_type=proposal_type,
            payload=payload,
            ranked=ranked,
            max_candidates=max_candidates,
            max_tokens=max_tokens,
            derivation_hints=derivation_hints,
        )
        tokens = self._estimate_tokens(
            proposal_type, payload, packed, derivation_hints=derivation_hints
        )
        # If still over budget, truncate reverse-priority: generic → supporting → pinned.
        budget_exceeded = tokens > max_tokens
        if budget_exceeded and packed:
            packed = self._truncate_by_tier(
                proposal_type=proposal_type,
                payload=payload,
                packed=packed,
                max_tokens=max_tokens,
                derivation_hints=derivation_hints,
            )
            tokens = self._estimate_tokens(
                proposal_type, payload, packed, derivation_hints=derivation_hints
            )
            budget_exceeded = tokens > max_tokens
            # Pinned may still not fit alone → fail closed.
            if budget_exceeded:
                packed = []
                tokens = self._estimate_tokens(
                    proposal_type, payload, packed, derivation_hints=derivation_hints
                )

        context = ReviewContext(
            proposal_type=proposal_type,
            proposal=dict(payload),
            concepts=packed,
            derivation_hints=derivation_hints,
            prefer_clarification=prefer_clarification,
            estimated_tokens=tokens,
            budget_exceeded=budget_exceeded,
            candidate_selection_trace=[
                {
                    "kind": item.kind,
                    "key": item.key,
                    "tier": item.tier.name.lower(),
                    "score": round(item.score, 4),
                    "trace": item.selection_trace or item.reason,
                }
                for item in packed
            ],
        )
        context.input_hash = _hash_context(context)
        return context

    def _merge_derivation_pins(
        self,
        *,
        namespace_key: str,
        ranked: list[ContextConcept],
        pin_concepts: tuple[tuple[str, str], ...],
    ) -> list[ContextConcept]:
        """Force-pin concepts referenced by derivation hints (compact, not whole graph)."""
        by_id = {item.identity(): item for item in ranked}
        for kind, key in pin_concepts:
            identity = (kind, key)
            if identity in by_id and by_id[identity].tier == ContextTier.PINNED:
                continue
            row = None
            if kind == "class":
                row = self._ontology.get_class_by_key(
                    namespace_key=namespace_key, class_key=key
                )
            elif kind == "predicate":
                row = self._ontology.get_predicate_by_key(
                    namespace_key=namespace_key, predicate_key=key
                )
            if row is None:
                continue
            by_id[identity] = _make_concept(
                self._ontology,
                kind=kind,
                row=row,
                score=0.99,
                reason="canonical_derivation",
                tier=ContextTier.PINNED,
                selection_trace="canonical_derivation",
            )
        return sorted(
            by_id.values(),
            key=lambda c: (int(c.tier), -c.score, c.kind, c.key),
        )

    def _pack_candidates(
        self,
        *,
        proposal_type: ProposalType,
        payload: dict[str, Any],
        ranked: list[ContextConcept],
        max_candidates: int,
        max_tokens: int,
        derivation_hints: list[str] | None = None,
    ) -> list[ContextConcept]:
        """Fill pinned first, then supporting, then generic — never let generics evict pins."""
        hints = derivation_hints or []
        by_tier = {
            ContextTier.PINNED: [c for c in ranked if c.tier == ContextTier.PINNED],
            ContextTier.SUPPORTING: [c for c in ranked if c.tier == ContextTier.SUPPORTING],
            ContextTier.GENERIC: [c for c in ranked if c.tier == ContextTier.GENERIC],
        }
        packed: list[ContextConcept] = []
        for tier in (ContextTier.PINNED, ContextTier.SUPPORTING, ContextTier.GENERIC):
            for concept in by_tier[tier]:
                if len(packed) >= max_candidates:
                    return packed
                trial = packed + [concept]
                if (
                    self._estimate_tokens(
                        proposal_type, payload, trial, derivation_hints=hints
                    )
                    > max_tokens
                    and packed
                ):
                    # Skip this candidate; try later ones only within same tier if smaller? No —
                    # keep order; stop filling this tier when budget blocks further adds.
                    if tier == ContextTier.PINNED:
                        # Keep pinned when possible; oversized single pins fail later.
                        if not packed:
                            packed.append(concept)
                        continue
                    break
                packed.append(concept)
        return packed

    def _truncate_by_tier(
        self,
        *,
        proposal_type: ProposalType,
        payload: dict[str, Any],
        packed: list[ContextConcept],
        max_tokens: int,
        derivation_hints: list[str] | None = None,
    ) -> list[ContextConcept]:
        remaining = list(packed)
        hints = derivation_hints or []
        # Drop from the end of each tier group: generics first, then supporting, pinned last.
        for tier in (ContextTier.GENERIC, ContextTier.SUPPORTING, ContextTier.PINNED):
            while (
                remaining
                and self._estimate_tokens(
                    proposal_type, payload, remaining, derivation_hints=hints
                )
                > max_tokens
            ):
                # Remove the last concept of this tier (lowest score within tier due to sort).
                idx = None
                for i in range(len(remaining) - 1, -1, -1):
                    if remaining[i].tier == tier:
                        idx = i
                        break
                if idx is None:
                    break
                remaining.pop(idx)
        return remaining

    def _retrieve_candidates(
        self,
        *,
        proposal_type: ProposalType,
        payload: dict[str, Any],
        embedding_candidates: list[dict[str, Any]],
    ) -> list[ContextConcept]:
        namespace_key = str(payload.get("namespace_key") or "core")
        key = str(payload.get("key") or payload.get("alias") or payload.get("child_key") or "")
        proposal_fields = _proposal_fields(payload, key=key)
        scored: dict[tuple[str, str], ContextConcept] = {}

        def add(concept: ContextConcept) -> None:
            identity = concept.identity()
            existing = scored.get(identity)
            if existing is None:
                scored[identity] = concept
                return
            # Prefer higher tier (lower enum), then higher score; keep best trace.
            if concept.tier < existing.tier or (
                concept.tier == existing.tier and concept.score > existing.score
            ):
                scored[identity] = concept

        # 1) Exact / normalized key match — PINNED
        if key:
            for kind, row in self._exact_matches(namespace_key=namespace_key, key=key):
                add(
                    _make_concept(
                        self._ontology,
                        kind=kind,
                        row=row,
                        score=1.0,
                        reason="exact",
                        tier=ContextTier.PINNED,
                        selection_trace="exact",
                    )
                )

        # 2) Alias match — PINNED
        if key:
            alias = self._ontology.find_alias(namespace_key=namespace_key, alias=key)
            if alias is not None:
                if alias.class_id is not None:
                    cls = self._ontology.get_class(alias.class_id)
                    if cls is not None:
                        add(
                            _make_concept(
                                self._ontology,
                                kind="class",
                                row=cls,
                                score=0.98,
                                reason="alias",
                                tier=ContextTier.PINNED,
                                selection_trace="alias",
                            )
                        )
                if alias.predicate_id is not None:
                    pred = self._ontology.get_predicate(alias.predicate_id)
                    if pred is not None:
                        add(
                            _make_concept(
                                self._ontology,
                                kind="predicate",
                                row=pred,
                                score=0.98,
                                reason="alias",
                                tier=ContextTier.PINNED,
                                selection_trace="alias",
                            )
                        )

        # 3) Lexical-nearest across ontology (key/label/description) — PINNED when strong
        lexical_hits = self._lexical_nearest(
            namespace_key=namespace_key,
            proposal_key=key,
            proposal_fields=proposal_fields,
            limit=12,
        )
        for concept in lexical_hits:
            add(concept)

        # 4) Embedding-nearest — PINNED when strong
        for item in embedding_candidates:
            kind = str(item.get("object_type") or "class")
            cand_key = str(item.get("key") or "")
            if not cand_key:
                continue
            emb_score = float(item.get("score") or 0.0)
            tier = ContextTier.PINNED if emb_score >= 0.75 else ContextTier.SUPPORTING
            add(
                ContextConcept(
                    kind=kind,
                    key=cand_key,
                    label=item.get("label"),
                    description=None,
                    score=0.5 + (0.5 * emb_score),
                    reason="embedding",
                    tier=tier,
                    selection_trace=f"embedding:{emb_score:.2f}",
                )
            )

        # 5) Same-parent siblings / same-domain-range peers, ranked by lexical proximity
        if proposal_type == ProposalType.CLASS:
            for parent_key in payload.get("parent_keys") or []:
                parent = self._ontology.get_class_by_key(
                    namespace_key=namespace_key, class_key=str(parent_key)
                )
                if parent is None:
                    continue
                parent_is_root = parent.key in {"Thing"}
                add(
                    _make_concept(
                        self._ontology,
                        kind="class",
                        row=parent,
                        score=0.55 if parent_is_root else 0.72,
                        reason="proposed_parent",
                        tier=ContextTier.GENERIC if parent_is_root else ContextTier.SUPPORTING,
                        selection_trace=(
                            "generic_parent" if parent_is_root else "proposed_parent"
                        ),
                    )
                )
                siblings: list[ContextConcept] = []
                for child_id in self._ontology.list_descendant_class_ids(parent.id):
                    if parent.id not in self._ontology.list_parent_class_ids(child_id):
                        continue
                    child = self._ontology.get_class(child_id)
                    if child is None or child.key == key:
                        continue
                    prox = _lexical_proximity(
                        proposal_fields,
                        _row_fields(self._ontology, "class", child),
                    )
                    if child.key in _HIGH_VALUE_CLASS_KEYS:
                        prox = max(prox, _HIGH_VALUE_PEER_FLOOR)
                    siblings.append(
                        _make_concept(
                            self._ontology,
                            kind="class",
                            row=child,
                            score=0.45 + (0.45 * prox),
                            reason="same_parent_sibling",
                            tier=(
                                ContextTier.SUPPORTING
                                if prox >= 0.35 or child.key in _HIGH_VALUE_CLASS_KEYS
                                else ContextTier.GENERIC
                            ),
                            selection_trace=f"same_parent:lexical:{prox:.2f}",
                        )
                    )
                siblings.sort(key=lambda c: (-c.score, c.key))
                for concept in siblings:
                    add(concept)
        elif proposal_type == ProposalType.PREDICATE:
            for domain_key in payload.get("domain_keys") or []:
                domain = self._ontology.get_class_by_key(
                    namespace_key=namespace_key, class_key=str(domain_key)
                )
                if domain is None:
                    continue
                domain_is_root = domain.key in {"Thing"}
                add(
                    _make_concept(
                        self._ontology,
                        kind="class",
                        row=domain,
                        score=0.55 if domain_is_root else 0.72,
                        reason="domain_class",
                        tier=ContextTier.GENERIC if domain_is_root else ContextTier.SUPPORTING,
                        selection_trace="domain_class",
                    )
                )
                peers: list[ContextConcept] = []
                for pred in self._ontology.list_predicates_for_domain_class(domain.id):
                    if pred.key == key:
                        continue
                    prox = _lexical_proximity(
                        proposal_fields,
                        _row_fields(self._ontology, "predicate", pred),
                    )
                    if pred.key in _HIGH_VALUE_PREDICATE_KEYS:
                        prox = max(prox, _HIGH_VALUE_PEER_FLOOR)
                    peers.append(
                        _make_concept(
                            self._ontology,
                            kind="predicate",
                            row=pred,
                            score=0.5 + (0.45 * prox),
                            reason="same_domain_range",
                            tier=(
                                ContextTier.SUPPORTING
                                if prox >= 0.3 or pred.key in _HIGH_VALUE_PREDICATE_KEYS
                                else ContextTier.GENERIC
                            ),
                            selection_trace=f"same_domain_range:lexical:{prox:.2f}",
                        )
                    )
                peers.sort(key=lambda c: (-c.score, c.key))
                for concept in peers:
                    add(concept)
            for range_key in payload.get("range_keys") or []:
                range_cls = self._ontology.get_class_by_key(
                    namespace_key=namespace_key, class_key=str(range_key)
                )
                if range_cls is None:
                    continue
                range_is_root = range_cls.key in {"Thing"}
                add(
                    _make_concept(
                        self._ontology,
                        kind="class",
                        row=range_cls,
                        score=0.55 if range_is_root else 0.7,
                        reason="range_class",
                        tier=ContextTier.GENERIC if range_is_root else ContextTier.SUPPORTING,
                        selection_trace="range_class",
                    )
                )

        # 6) Directly connected ontology concepts for already selected pinned/supporting
        seed = [
            c
            for c in scored.values()
            if c.tier in {ContextTier.PINNED, ContextTier.SUPPORTING}
        ]
        for concept in list(seed):
            self._add_connected_support(
                scored=scored,
                add=add,
                namespace_key=namespace_key,
                concept=concept,
            )

        # Sort: tier first (pinned→supporting→generic), then score desc, then key.
        return sorted(
            scored.values(),
            key=lambda c: (int(c.tier), -c.score, c.kind, c.key),
        )

    def _lexical_nearest(
        self,
        *,
        namespace_key: str,
        proposal_key: str,
        proposal_fields: _TextFields,
        limit: int,
    ) -> list[ContextConcept]:
        """Score classes/predicates against proposal fields; pin strong near-duplicates."""
        hits: list[ContextConcept] = []
        # Search seeds from key/label tokens only — description mentions are too noisy.
        seed_text = f"{proposal_fields.key} {proposal_fields.label}"
        tokens = _tokens(seed_text)
        queries = {proposal_key, *_camel_parts(proposal_key), *list(tokens)[:6]}
        seen_ids: set[tuple[str, str]] = set()
        candidates: list[tuple[str, Any]] = []
        for query in queries:
            if not query or len(query) < 2:
                continue
            for cls in self._ontology.search_classes(
                query=query, namespace_key=namespace_key, limit=25
            ):
                identity = ("class", cls.key)
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                candidates.append(("class", cls))
            for pred in self._ontology.search_predicates(
                query=query, namespace_key=namespace_key, limit=25
            ):
                identity = ("predicate", pred.key)
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                candidates.append(("predicate", pred))

        # Always score high-value calibration / core concepts when present.
        for kind_key in (
            ("class", "Role"),
            ("class", "Goal"),
            ("class", "Preference"),
            ("class", "Skill"),
            ("class", "System"),
            ("class", "Agent"),
            ("class", "Organization"),
            ("class", "Person"),
            ("class", "RelationshipContext"),
            ("predicate", "dependsOn"),
            ("predicate", "relatedTo"),
            ("predicate", "holdsRole"),
            ("predicate", "hasGoal"),
            ("predicate", "hasSkill"),
            ("predicate", "hasPreference"),
        ):
            kind, cand_key = kind_key
            if (kind, cand_key) in seen_ids:
                continue
            if kind == "class":
                row = self._ontology.get_class_by_key(
                    namespace_key=namespace_key, class_key=cand_key
                )
            else:
                row = self._ontology.get_predicate_by_key(
                    namespace_key=namespace_key, predicate_key=cand_key
                )
            if row is not None:
                seen_ids.add((kind, cand_key))
                candidates.append((kind, row))

        for kind, row in candidates:
            if row.key == proposal_key:
                continue
            prox = _lexical_proximity(
                proposal_fields,
                _row_fields(self._ontology, kind, row),
            )
            if prox < 0.28:
                continue
            tier = ContextTier.PINNED if prox >= 0.55 else ContextTier.SUPPORTING
            hits.append(
                _make_concept(
                    self._ontology,
                    kind=kind,
                    row=row,
                    score=prox,
                    reason="lexical",
                    tier=tier,
                    selection_trace=f"lexical:{prox:.2f}",
                )
            )
        hits.sort(key=lambda c: (-c.score, c.kind, c.key))
        return hits[:limit]

    def _add_connected_support(
        self,
        *,
        scored: dict[tuple[str, str], ContextConcept],
        add: Any,
        namespace_key: str,
        concept: ContextConcept,
    ) -> None:
        """Add short descriptions' neighbor classes for pinned/supporting concepts."""
        if concept.kind == "class":
            row = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=concept.key
            )
            if row is None:
                return
            for parent_id in self._ontology.list_parent_class_ids(row.id)[:3]:
                parent = self._ontology.get_class(parent_id)
                if parent is None:
                    continue
                is_root = parent.key in {"Thing"}
                add(
                    _make_concept(
                        self._ontology,
                        kind="class",
                        row=parent,
                        score=0.4 if is_root else 0.58,
                        reason="connected_parent",
                        tier=ContextTier.GENERIC if is_root else ContextTier.SUPPORTING,
                        selection_trace=f"connected:{concept.key}->parent",
                    )
                )
        elif concept.kind == "predicate":
            pred = self._ontology.get_predicate_by_key(
                namespace_key=namespace_key, predicate_key=concept.key
            )
            if pred is None or pred.current_revision_id is None:
                return
            for class_id in self._ontology.list_domain_class_ids(pred.current_revision_id)[:3]:
                cls = self._ontology.get_class(class_id)
                if cls is None:
                    continue
                is_root = cls.key in {"Thing"}
                add(
                    _make_concept(
                        self._ontology,
                        kind="class",
                        row=cls,
                        score=0.4 if is_root else 0.56,
                        reason="connected_domain",
                        tier=ContextTier.GENERIC if is_root else ContextTier.SUPPORTING,
                        selection_trace=f"connected:{concept.key}->domain",
                    )
                )
            for class_id in self._ontology.list_range_class_ids(pred.current_revision_id)[:3]:
                cls = self._ontology.get_class(class_id)
                if cls is None:
                    continue
                is_root = cls.key in {"Thing"}
                add(
                    _make_concept(
                        self._ontology,
                        kind="class",
                        row=cls,
                        score=0.4 if is_root else 0.56,
                        reason="connected_range",
                        tier=ContextTier.GENERIC if is_root else ContextTier.SUPPORTING,
                        selection_trace=f"connected:{concept.key}->range",
                    )
                )

    def _exact_matches(self, *, namespace_key: str, key: str) -> list[tuple[str, Any]]:
        matches: list[tuple[str, Any]] = []
        cls = self._ontology.get_class_by_key(namespace_key=namespace_key, class_key=key)
        if cls is not None:
            matches.append(("class", cls))
        pred = self._ontology.get_predicate_by_key(namespace_key=namespace_key, predicate_key=key)
        if pred is not None:
            matches.append(("predicate", pred))
        for cls in self._ontology.search_classes(query=key, namespace_key=namespace_key, limit=5):
            if normalize_text(cls.key) == normalize_text(key):
                matches.append(("class", cls))
        for pred in self._ontology.search_predicates(
            query=key, namespace_key=namespace_key, limit=5
        ):
            if normalize_text(pred.key) == normalize_text(key):
                matches.append(("predicate", pred))
        return matches

    def _estimate_tokens(
        self,
        proposal_type: ProposalType,
        payload: dict[str, Any],
        concepts: list[ContextConcept],
        *,
        derivation_hints: list[str] | None = None,
    ) -> int:
        system_overhead = 520
        proposal_chars = len(json.dumps({"type": proposal_type.value, **_public_payload(payload)}))
        concept_chars = sum(
            len(c.kind)
            + len(c.key)
            + len(c.label or "")
            + len(c.description or "")
            + len(c.reason)
            + 24
            for c in concepts
        )
        hint_chars = sum(len(h) + 4 for h in (derivation_hints or []))
        return system_overhead + max(1, (proposal_chars + concept_chars + hint_chars) // 4)


def lexical_similarity_candidates(
    session: Session,
    *,
    proposal_type: ProposalType,
    payload: dict[str, Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Cheap local lexical near-matches for rejection feedback (no external cost)."""
    builder = SemanticReviewContextBuilder(session)
    context = builder.build(proposal_type=proposal_type, payload=payload)
    out: list[dict[str, Any]] = []
    for item in context.concepts:
        if item.tier != ContextTier.PINNED and not item.selection_trace.startswith("lexical"):
            continue
        out.append(
            {
                "object_type": item.kind,
                "key": item.key,
                "label": item.label,
                "score": item.score,
                "reason": item.selection_trace or item.reason,
            }
        )
        if len(out) >= limit:
            break
    return out


def _make_concept(
    repo: OntologyRepository,
    *,
    kind: str,
    row: Any,
    score: float,
    reason: str,
    tier: ContextTier,
    selection_trace: str,
) -> ContextConcept:
    return ContextConcept(
        kind=kind,
        key=row.key,
        label=_label_for(repo, kind, row),
        description=_description_for(repo, kind, row),
        score=score,
        reason=reason,
        tier=tier,
        selection_trace=selection_trace,
    )


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    metadata = dict(cleaned.get("metadata") or {})
    metadata.pop("review_decision", None)
    metadata.pop("review_result", None)
    if metadata:
        cleaned["metadata"] = metadata
    else:
        cleaned.pop("metadata", None)
    cleaned.pop("review_decision", None)
    return cleaned


def match_derivation_hints(
    *,
    proposal_type: ProposalType,
    payload: dict[str, Any],
) -> DerivationHintMatch | None:
    """Return compact canonical-model hints for proposals that often denormalize."""
    key = str(payload.get("key") or payload.get("alias") or payload.get("child_key") or "")
    label = str(payload.get("label") or "")
    description = str(payload.get("description") or "")
    key_cf = key.casefold()
    blob = f"{key} {label} {description}".casefold()

    if proposal_type == ProposalType.PREDICATE:
        if key_cf in {
            "employedby",
            "hasemployer",
            "hascurrentemployer",
            "employerof",
        } or "employed by" in blob or "current employer" in blob:
            return DerivationHintMatch(
                hints=(
                    "Relevant canonical path: Person --holdsRole--> Role --roleAt--> Organization",
                    "Prefer this composition over a direct Person→Organization employment shortcut "
                    "unless distinct non-derivable semantics are proven.",
                ),
                pin_concepts=(
                    ("predicate", "holdsRole"),
                    ("predicate", "roleAt"),
                    ("class", "Person"),
                    ("class", "Role"),
                    ("class", "Organization"),
                    ("class", "Employment"),
                ),
            )
        if key_cf in {"currentrole", "hascurrentrole"} or "current role" in blob:
            return DerivationHintMatch(
                hints=(
                    "Relevant canonical relation: Agent/Person --holdsRole--> Role",
                    "Temporal validity (current vs historical) is stored on the statement "
                    "(valid_from/valid_to / as_of), not as a separate predicate.",
                ),
                pin_concepts=(
                    ("predicate", "holdsRole"),
                    ("class", "Role"),
                    ("class", "Agent"),
                    ("class", "Person"),
                ),
            )
        if key_cf in {"usedby", "uses"}:
            return DerivationHintMatch(
                hints=(
                    "Directionality matters: usedBy/uses is not automatically dependsOn or deployedOn.",
                    "If intended subject/object or usage semantics are underspecified, prefer "
                    "manual_review with clarification over approval.",
                ),
                pin_concepts=(
                    ("predicate", "dependsOn"),
                    ("predicate", "deployedOn"),
                    ("predicate", "relatedTo"),
                    ("class", "System"),
                    ("class", "Agent"),
                ),
            )
        if key_cf in {"avoids", "avoid"}:
            return DerivationHintMatch(
                hints=(
                    "Negative semantics may be a relation, Preference, or Constraint depending on "
                    "subject/object and hardness.",
                    "Prefer clarification over approval when interpretation is underspecified.",
                ),
                pin_concepts=(
                    ("predicate", "relatedTo"),
                    ("class", "Preference"),
                    ("class", "Constraint"),
                ),
            )
        if key_cf in {"partof", "haspart"}:
            return DerivationHintMatch(
                hints=(
                    "partOf/hasPart needs explicit domain, direction, and whether transitivity is "
                    "intended; otherwise prefer manual_review.",
                ),
                pin_concepts=(
                    ("predicate", "relatedTo"),
                    ("predicate", "dependsOn"),
                ),
            )

    if proposal_type == ProposalType.CLASS:
        if key_cf in {"historicalrole", "formerrole", "pastrole"} or (
            "historical" in blob and "role" in blob
        ):
            return DerivationHintMatch(
                hints=(
                    "Relevant canonical relation: Agent/Person --holdsRole--> Role",
                    "Temporality belongs on statements/validity windows, not a HistoricalRole class.",
                ),
                pin_concepts=(
                    ("class", "Role"),
                    ("predicate", "holdsRole"),
                ),
            )
        if key_cf.startswith("verified") or key_cf.startswith("confirmed") or (
            "verified" in blob and "skill" in blob
        ):
            return DerivationHintMatch(
                hints=(
                    "Skill assertions support provenance/evidence metadata.",
                    "Do not mint Verified*/Confirmed* classes when verification is only an "
                    "evidential qualifier on an existing concept.",
                ),
                pin_concepts=(("class", "Skill"), ("predicate", "hasSkill")),
            )
        if key_cf.startswith("probable") or key_cf.startswith("likely") or (
            "confidence" in blob and ("goal" in blob or "probable" in blob)
        ):
            return DerivationHintMatch(
                hints=(
                    "Confidence belongs on the assertion, not as a Probable*/Likely* class "
                    "in the taxonomy.",
                ),
                pin_concepts=(("class", "Goal"), ("predicate", "hasGoal")),
            )
        if key_cf in {"employmentrelation", "employment"} or (
            "ongoing employment" in blob or "employment relationship" in blob
        ):
            return DerivationHintMatch(
                hints=(
                    "Existing Employment (RelationshipContext) already models ongoing employment.",
                    "Also consider Person --holdsRole--> Role --roleAt--> Organization before "
                    "approving a parallel employment class.",
                ),
                pin_concepts=(
                    ("class", "Employment"),
                    ("class", "RelationshipContext"),
                    ("class", "Role"),
                    ("predicate", "holdsRole"),
                    ("predicate", "roleAt"),
                ),
            )
        if key_cf in {"professionalskill"} or (
            "professional" in blob and "skill" in blob and "role" in blob
        ):
            return DerivationHintMatch(
                hints=(
                    "Professional/role-contextualized skills are often representable via hasSkill "
                    "plus Role/context rather than a new primitive class.",
                    "Ask whether hasSkill + Role/context already suffices. Prefer manual_review "
                    "with clarification over approve or reuse_existing on the first pass.",
                ),
                pin_concepts=(
                    ("class", "Skill"),
                    ("class", "Role"),
                    ("predicate", "hasSkill"),
                ),
                prefer_clarification=True,
            )
        # Core leakage: person-named, product-scoped, or narrow domain concepts.
        if (
            key_cf.startswith("didac")
            or ("personal" in blob and "goal" in blob)
            or "only inside" in blob
            or "product experiment" in blob
            or "quantitative finance" in blob
            or "trading signal" in blob
        ):
            return DerivationHintMatch(
                hints=(
                    "User-, product-, or domain-specific concepts should not enter core unless "
                    "reusable across domains; prefer entity instances or a domain namespace.",
                ),
                pin_concepts=(
                    ("class", "Goal"),
                    ("class", "Observation"),
                    ("class", "Document"),
                ),
            )

    return None


def _hash_context(context: ReviewContext) -> str:
    payload = {
        "builder": context.builder_version,
        "proposal_type": context.proposal_type.value,
        "proposal": _public_payload(context.proposal),
        "derivation_hints": list(context.derivation_hints),
        "prefer_clarification": context.prefer_clarification,
        "concepts": [
            {
                "kind": c.kind,
                "key": c.key,
                "reason": c.reason,
                "score": c.score,
                "tier": c.tier.name,
                "trace": c.selection_trace,
            }
            for c in context.concepts
        ],
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _label_for(repo: OntologyRepository, kind: str, row: Any) -> str | None:
    if kind == "class":
        revision = repo.get_current_class_revision(row)
        return None if revision is None else revision.label
    revision = repo.get_current_predicate_revision(row)
    return None if revision is None else revision.label


def _description_for(repo: OntologyRepository, kind: str, row: Any) -> str | None:
    if kind == "class":
        revision = repo.get_current_class_revision(row)
        return None if revision is None else revision.description
    revision = repo.get_current_predicate_revision(row)
    return None if revision is None else revision.description


@dataclass(frozen=True)
class _TextFields:
    key: str
    label: str
    description: str


def _row_fields(repo: OntologyRepository, kind: str, row: Any) -> _TextFields:
    return _TextFields(
        key=str(row.key or ""),
        label=str(_label_for(repo, kind, row) or ""),
        description=str(_description_for(repo, kind, row) or ""),
    )


def _proposal_fields(payload: dict[str, Any], *, key: str) -> _TextFields:
    # Parent/domain/range keys are structural hints handled separately — do not
    # feed them into lexical scoring (they false-pin Thing/roots).
    return _TextFields(
        key=key,
        label=str(payload.get("label") or ""),
        description=str(payload.get("description") or ""),
    )


def _camel_parts(value: str) -> list[str]:
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", value)
    return [p for p in parts if p]


def _tokens(value: str) -> set[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return set(re.findall(r"[a-z0-9]+", normalize_text(spaced)))


def _compact_key(value: str) -> str:
    return normalize_text("".join(ch for ch in value if ch.isalnum()))


def _content_tokens(value: str) -> set[str]:
    return {_light_stem(tok) for tok in _tokens(value) if len(tok) >= 4}


def _light_stem(token: str) -> str:
    t = token.casefold()
    for suf in (
        "ational",
        "ization",
        "isation",
        "ation",
        "ition",
        "ment",
        "ness",
        "ally",
        "ing",
        "ers",
        "ies",
        "es",
        "ed",
        "ly",
        "s",
    ):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            t = t[: -len(suf)]
            break
    return t


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _stem_part_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    best = 0.0
    for a in left:
        sa = _light_stem(a)
        if len(sa) < 4:
            continue
        for b in right:
            sb = _light_stem(b)
            if len(sb) < 4:
                continue
            if sa == sb or sa.startswith(sb[:4]) or sb.startswith(sa[:4]):
                best = max(best, 1.0 if sa == sb else 0.7)
    return best


def _lexical_proximity(proposal: _TextFields, candidate: _TextFields) -> float:
    """Relevance score for retrieval. Key/label dominate; bare description mentions do not pin."""
    pk = _compact_key(proposal.key)
    ck = _compact_key(candidate.key)
    if not pk or not ck:
        return 0.0
    if pk == ck:
        return 1.0

    contain = 0.0
    if min(len(pk), len(ck)) >= 4 and (pk in ck or ck in pk):
        contain = 0.92

    key_ratio = SequenceMatcher(None, pk, ck).ratio()
    prop_id_parts = {
        normalize_text(p) for p in _camel_parts(proposal.key)
    } | _tokens(proposal.label)
    cand_id_parts = {
        normalize_text(p) for p in _camel_parts(candidate.key)
    } | _tokens(candidate.label)
    stem_hit = _stem_part_overlap(prop_id_parts, cand_id_parts)

    desc_j = _jaccard(
        _content_tokens(proposal.description),
        _content_tokens(candidate.description),
    )
    # Cross identity↔description vocabulary (JobTitle "position" vs Role description).
    cross_j = max(
        _jaccard(
            _content_tokens(f"{proposal.key} {proposal.label} {proposal.description}"),
            _content_tokens(f"{candidate.key} {candidate.label}"),
        ),
        _jaccard(
            _content_tokens(f"{proposal.key} {proposal.label}"),
            _content_tokens(f"{candidate.key} {candidate.label} {candidate.description}"),
        ),
        desc_j,
    )

    # SequenceMatcher on short keys is noisy below ~0.5 (Person/Responsibility ≈ 0.40).
    if key_ratio >= 0.55 or (key_ratio >= 0.48 and stem_hit >= 0.7):
        ratio_score = 0.4 + 0.6 * key_ratio
    else:
        ratio_score = 0.35 * key_ratio

    identity = max(contain, ratio_score, 0.8 * stem_hit)
    text_score = (0.35 + 0.55 * cross_j) if cross_j >= 0.2 else (0.7 * cross_j)

    # Candidate key/label mentioned in proposal description: strong only with other signal.
    cand_mention_parts = {
        _light_stem(normalize_text(p)) for p in _camel_parts(candidate.key)
    } | {_light_stem(ck)}
    cand_mention_parts |= {_light_stem(t) for t in _tokens(candidate.label) if len(t) >= 4}
    prop_desc_stems = _content_tokens(proposal.description)
    mentioned = bool(cand_mention_parts & prop_desc_stems)
    if mentioned and (
        cross_j >= 0.15 or identity >= 0.45 or contain or key_ratio >= 0.48
    ):
        # key_ratio gate keeps reliesUpon↔dependsOn strong while "not a Role" stays weak.
        mention_score = 0.82
    elif mentioned:
        # Contrastive mentions ("not a Role") must not outrank true neighbors.
        mention_score = 0.18
    else:
        mention_score = 0.0

    # Description vocabulary overlap: pin near-duplicates like JobTitle↔Role.
    if cross_j >= 0.25:
        text_score = max(text_score, 0.4 + 0.55 * cross_j)

    return min(1.0, max(identity, text_score, mention_score))
