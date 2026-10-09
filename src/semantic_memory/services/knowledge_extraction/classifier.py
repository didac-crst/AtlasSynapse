"""Structure-aware deterministic semantic classifier.

Path segments and leaf wording are **evidence** that extraction uses to set
``kind`` / ``polarity`` / ``epistemic_status`` explicitly on the draft.

Commit-time code must not re-derive status from path strings; this module is
where that decision is made and recorded on the candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from semantic_memory.models.enums import (
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
)
from semantic_memory.services.knowledge_extraction.candidate_keys import build_candidate_key
from semantic_memory.services.knowledge_extraction.schemas import (
    CandidateDraft,
    FragmentSkip,
)
from semantic_memory.services.knowledge_extraction.structural import SourceFragment

EXTRACTOR_VERSION = "knowledge-extraction-v1"

_METADATA_KEYS = frozenset(
    {
        "package_id",
        "schema_version",
        "generated_at",
        "generated_by",
        "version",
        "id",
        "uuid",
        "title",
        "description",
        "notes_meta",
        "format",
        "encoding",
        "content_type",
        "mime_type",
        "formatting_helper",
        "non_semantic",
    }
)

# Contextual / illustrative structures — not world facts unless a leaf is
# explicitly marked as a supported semantic kind.
_CONTEXTUAL_SKIP_TOKENS = frozenset(
    {
        "example",
        "examples",
        "illustration",
        "illustrations",
        "caveat",
        "caveats",
        "anti_use_case",
        "anti_use_cases",
        "comparison",
        "comparisons",
        "comparisons_to",
        "illustrative",
    }
)

_EXPLICIT_SEMANTIC_LEAF_KEYS = frozenset(
    {
        "statement",
        "claim",
        "assertion",
        "hypothesis",
        "hypotheses",
        "question",
        "questions",
        "recommendation",
        "recommendations",
    }
)

_QUESTION_RE = re.compile(r"\?\s*$")
_NEG_REC_RE = re.compile(
    r"\b(should not|must not|ought not|do not|don't|cannot|can't|never)\b",
    re.IGNORECASE,
)
_POS_REC_RE = re.compile(r"\b(should|must|ought|recommend|prefer)\b", re.IGNORECASE)
_HYP_RE = re.compile(
    r"\b(maybe|might|perhaps|possibly|hypothesis|hypothesize|could be)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _ContextRole:
    kind_hint: KnowledgeCandidateKind | None
    epistemic_status: KnowledgeCandidateEpistemicStatus | None
    polarity_hint: KnowledgeCandidatePolarity | None
    labels: tuple[str, ...]


def classify_fragments(
    fragments: list[SourceFragment],
) -> tuple[list[CandidateDraft], list[FragmentSkip]]:
    drafts: list[CandidateDraft] = []
    skips: list[FragmentSkip] = []
    # Prefer scalar leaves; mapping/sequence containers are skipped unless a
    # specialized leaf (e.g. statement field) is extracted from children.
    for fragment in fragments:
        decision = _classify_one(fragment)
        if isinstance(decision, FragmentSkip):
            skips.append(decision)
        elif decision is not None:
            drafts.append(decision)
    return drafts, skips


def _classify_one(fragment: SourceFragment) -> CandidateDraft | FragmentSkip | None:
    path = fragment.path_list()
    if not path:
        return FragmentSkip(path=[], reason="container", detail="document root")

    leaf_key = fragment.parent_key or (str(path[-1]) if path else None)
    leaf_norm = _normalize_key(leaf_key) if leaf_key is not None else None
    path_tokens = {_normalize_key(str(p)) for p in path if isinstance(p, str)}
    if path_tokens & _METADATA_KEYS:
        hit = sorted(path_tokens & _METADATA_KEYS)[0]
        return FragmentSkip(path=path, reason="metadata", detail=f"metadata path '{hit}'")
    if leaf_norm is not None and leaf_norm in _METADATA_KEYS:
        return FragmentSkip(path=path, reason="metadata", detail=f"metadata key '{leaf_key}'")

    if fragment.structural_type in {"mapping", "sequence"}:
        return FragmentSkip(path=path, reason="container", detail=fragment.structural_type)

    if fragment.structural_type == "null":
        return FragmentSkip(path=path, reason="empty", detail="null value")

    # Nested object fields that are non-propositional.
    if leaf_norm in {
        "confidence",
        "score",
        "weight",
        "rank",
        "order",
        "priority",
    }:
        return FragmentSkip(path=path, reason="non_semantic", detail=f"metric '{leaf_key}'")

    contextual_hits = path_tokens & _CONTEXTUAL_SKIP_TOKENS
    explicitly_marked = leaf_norm in _EXPLICIT_SEMANTIC_LEAF_KEYS if leaf_norm else False
    if contextual_hits and not explicitly_marked:
        hit = sorted(contextual_hits)[0]
        return FragmentSkip(
            path=path,
            reason="unsupported_semantics",
            detail=f"contextual structure '{hit}' without explicit semantic leaf",
        )

    role = _infer_context_role(path)
    text = _proposition_text(fragment)
    if text is None:
        return FragmentSkip(
            path=path,
            reason="unsupported_value",
            detail=f"cannot form proposition from {fragment.structural_type}",
        )
    if not text.strip():
        return FragmentSkip(path=path, reason="empty", detail="blank text")

    kind, polarity, epistemic, derivation, claim_text, basis, payload = _decide_semantics(
        text=text,
        fragment=fragment,
        role=role,
        leaf_key=leaf_key,
    )

    # Never emit an *active positive assertion* for rejected alternatives.
    if any("rejected_option" in lab for lab in role.labels):
        if (
            kind == KnowledgeCandidateKind.ASSERTION
            and polarity == KnowledgeCandidatePolarity.POSITIVE
            and epistemic == KnowledgeCandidateEpistemicStatus.ACTIVE
        ):
            return FragmentSkip(
                path=path,
                reason="unsupported_semantics",
                detail="rejected alternative must not become active positive assertion",
            )

    ordinal = 0
    key = build_candidate_key(source_context_path=path, ordinal=ordinal)
    return CandidateDraft(
        candidate_key=key,
        kind=kind,
        polarity=polarity,
        epistemic_status=epistemic,
        derivation=derivation,
        claim_text=claim_text.strip(),
        claim_payload=payload,
        source_span=fragment.locator(),
        source_context_path=path,
        ordinal=ordinal,
        classification_basis=basis,
    )


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().casefold()).strip("_")


def _infer_context_role(path: list[str | int]) -> _ContextRole:
    labels: list[str] = []
    kind_hint: KnowledgeCandidateKind | None = None
    epistemic: KnowledgeCandidateEpistemicStatus | None = None
    polarity: KnowledgeCandidatePolarity | None = None

    for part in path:
        if not isinstance(part, str):
            continue
        token = _normalize_key(part)
        labels.append(token)

        if any(
            token.startswith(p) or p in token
            for p in (
                "rejected",
                "weakened",
                "discarded",
                "invalidated",
            )
        ):
            epistemic = KnowledgeCandidateEpistemicStatus.REJECTED
            if kind_hint is None:
                kind_hint = KnowledgeCandidateKind.HYPOTHESIS

        if any(p in token for p in ("open_question", "questions", "question")):
            kind_hint = KnowledgeCandidateKind.QUESTION
            if epistemic is None:
                epistemic = KnowledgeCandidateEpistemicStatus.OPEN

        if any(p in token for p in ("recommend", "recommended", "recommendation")):
            kind_hint = KnowledgeCandidateKind.RECOMMENDATION
            if epistemic is None:
                epistemic = KnowledgeCandidateEpistemicStatus.ACTIVE

        if any(p in token for p in ("hypothes",)):
            kind_hint = KnowledgeCandidateKind.HYPOTHESIS
            if epistemic is None:
                epistemic = KnowledgeCandidateEpistemicStatus.ACTIVE

        if any(p in token for p in ("assertion", "claims", "facts", "findings")):
            if kind_hint is None:
                kind_hint = KnowledgeCandidateKind.ASSERTION
            if epistemic is None:
                epistemic = KnowledgeCandidateEpistemicStatus.ACTIVE

        if token in {"rejected_options", "rejected_option"}:
            kind_hint = KnowledgeCandidateKind.ASSERTION
            epistemic = KnowledgeCandidateEpistemicStatus.REJECTED

    return _ContextRole(
        kind_hint=kind_hint,
        epistemic_status=epistemic,
        polarity_hint=polarity,
        labels=tuple(labels),
    )


def _proposition_text(fragment: SourceFragment) -> str | None:
    value = fragment.raw_value
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        key = fragment.parent_key or "value"
        return f"{key}: {value}"
    if isinstance(value, dict):
        # Prefer explicit statement / hypothesis / text fields.
        for field in ("statement", "hypothesis", "question", "recommendation", "text", "claim"):
            nested = value.get(field)
            if isinstance(nested, str):
                return nested
        return None
    return None


def _decide_semantics(
    *,
    text: str,
    fragment: SourceFragment,
    role: _ContextRole,
    leaf_key: str | None,
) -> tuple[
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateDerivation,
    str,
    list[str],
    dict[str, Any],
]:
    basis: list[str] = []
    kind = role.kind_hint
    epistemic = role.epistemic_status
    polarity = KnowledgeCandidatePolarity.POSITIVE
    derivation = KnowledgeCandidateDerivation.EXPLICIT
    claim_text = text.strip()

    if role.labels:
        basis.append("context_path=" + "/".join(role.labels))

    # Leaf key nuances.
    if leaf_key is not None:
        nk = _normalize_key(leaf_key)
        if nk in {"hypothesis", "hypotheses"}:
            kind = KnowledgeCandidateKind.HYPOTHESIS
            basis.append("leaf_key=hypothesis")
        elif nk in {"question", "questions"}:
            kind = KnowledgeCandidateKind.QUESTION
            epistemic = epistemic or KnowledgeCandidateEpistemicStatus.OPEN
            basis.append("leaf_key=question")
        elif nk in {"recommendation", "recommendations"}:
            kind = KnowledgeCandidateKind.RECOMMENDATION
            basis.append("leaf_key=recommendation")
        elif nk in {"statement", "claim", "assertion"}:
            kind = kind or KnowledgeCandidateKind.ASSERTION
            basis.append(f"leaf_key={nk}")
        elif nk == "license" and (
            kind == KnowledgeCandidateKind.RECOMMENDATION
            or any("recommend" in lab for lab in role.labels)
        ):
            kind = KnowledgeCandidateKind.RECOMMENDATION
            if isinstance(fragment.raw_value, str):
                claim_text = f"Recommended license: {fragment.raw_value}"
            derivation = KnowledgeCandidateDerivation.NORMALIZED
            basis.append("leaf_key=license_under_recommended")

    # Textual cues (do not override an explicit rejected epistemic from context).
    if _QUESTION_RE.search(claim_text):
        kind = KnowledgeCandidateKind.QUESTION
        epistemic = KnowledgeCandidateEpistemicStatus.OPEN
        basis.append("text_ends_with_question_mark")

    if _NEG_REC_RE.search(claim_text):
        kind = kind or KnowledgeCandidateKind.RECOMMENDATION
        if kind == KnowledgeCandidateKind.ASSERTION:
            kind = KnowledgeCandidateKind.RECOMMENDATION
        polarity = KnowledgeCandidatePolarity.NEGATIVE
        basis.append("text_negative_recommendation_cue")
    elif _POS_REC_RE.search(claim_text) and kind in {
        None,
        KnowledgeCandidateKind.ASSERTION,
        KnowledgeCandidateKind.RECOMMENDATION,
    }:
        kind = KnowledgeCandidateKind.RECOMMENDATION
        basis.append("text_positive_recommendation_cue")

    if _HYP_RE.search(claim_text) and kind in {None, KnowledgeCandidateKind.ASSERTION}:
        kind = KnowledgeCandidateKind.HYPOTHESIS
        basis.append("text_hypothesis_cue")

    if kind is None:
        kind = KnowledgeCandidateKind.ASSERTION
        basis.append("default_kind=assertion")

    if epistemic is None:
        if kind == KnowledgeCandidateKind.QUESTION:
            epistemic = KnowledgeCandidateEpistemicStatus.OPEN
        else:
            epistemic = KnowledgeCandidateEpistemicStatus.ACTIVE
        basis.append(f"default_epistemic={epistemic.value}")
    else:
        # Explicit extraction decision — recorded on the candidate.
        basis.append(f"epistemic_status={epistemic.value}")

    # Rejected ≠ negative polarity.
    if epistemic == KnowledgeCandidateEpistemicStatus.REJECTED:
        basis.append("rejected_keeps_polarity_separate")

    payload = _build_payload_hint(text=claim_text, kind=kind, fragment=fragment)
    if (
        derivation == KnowledgeCandidateDerivation.EXPLICIT
        and isinstance(fragment.raw_value, str)
        and claim_text.strip() != fragment.raw_value.strip()
    ):
        derivation = KnowledgeCandidateDerivation.NORMALIZED
        basis.append("derivation=normalized_wording")

    if (
        derivation == KnowledgeCandidateDerivation.EXPLICIT
        and not isinstance(fragment.raw_value, str)
        and kind == KnowledgeCandidateKind.RECOMMENDATION
    ):
        derivation = KnowledgeCandidateDerivation.NORMALIZED
        basis.append("derivation=normalized_from_scalar")

    return kind, polarity, epistemic, derivation, claim_text, basis, payload


def _build_payload_hint(
    *, text: str, kind: KnowledgeCandidateKind, fragment: SourceFragment
) -> dict[str, Any]:
    payload: dict[str, Any] = {"raw": fragment.raw_value, "extractor_version": EXTRACTOR_VERSION}
    # Lightweight SPO hinting — strings only, never IDs.
    subject_match = re.match(
        r"^(?P<subject>[A-Z][\w.-]*(?:\s+[A-Z][\w.-]*)*)\s+(?P<rest>.+)$",
        text.strip(),
    )
    if subject_match and kind != KnowledgeCandidateKind.QUESTION:
        subject = subject_match.group("subject")
        rest = subject_match.group("rest")
        payload["subject"] = {"text": subject}
        if _NEG_REC_RE.search(text):
            payload["predicate_text"] = "should_not"
            payload["predicate_key_hint"] = "shouldNot"
            obj = re.sub(
                r"^(should not|must not|ought not|do not|don't)\s+",
                "",
                rest,
                flags=re.IGNORECASE,
            ).strip(" .")
            if obj:
                payload["object"] = {"text": obj}
        elif m := re.match(
            r"^(?:should|must|ought to|recommends?)\s+(?P<object>.+)$",
            rest,
            flags=re.IGNORECASE,
        ):
            payload["predicate_text"] = "should"
            payload["predicate_key_hint"] = "should"
            payload["object"] = {"text": m.group("object").strip(" .")}
        elif m := re.match(
            r"^(?:may|might|could)\s+(?P<pred>\w+)\s+(?:through|via|by)\s+(?P<object>.+)$",
            rest,
            flags=re.IGNORECASE,
        ):
            payload["predicate_key_hint"] = m.group("pred")
            payload["predicate_text"] = m.group("pred")
            payload["object"] = {"text": m.group("object").strip(" .")}
    return payload
