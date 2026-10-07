"""Tokenized lexical matching for retrieval candidate generation.

Punctuation-insensitive tokenization with a small stopword list. Temporal and
question words are dropped so they do not force full-string ILIKE failures and
do not dilute multi-token coverage (temporal/predicate intent comes later).
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Kept small and retrieval-specific. Not a general NLP stopword list.
_LEXICAL_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "from",
        "with",
        "about",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "having",
        "who",
        "what",
        "where",
        "when",
        "why",
        "how",
        "which",
        "whom",
        "whose",
        "his",
        "her",
        "hers",
        "him",
        "their",
        "theirs",
        "them",
        "my",
        "mine",
        "our",
        "ours",
        "your",
        "yours",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        # Temporal intent reserved for a later retrieval stage.
        "current",
        "currently",
        "now",
        "today",
        "latest",
        "former",
        "formerly",
        "previous",
        "previously",
        "before",
        "after",
        "ago",
        # Soft query fillers.
        "please",
        "tell",
        "me",
        "show",
        "find",
        "get",
        "list",
        "any",
        "some",
        "all",
    }
)


def escape_ilike(fragment: str) -> str:
    """Escape ``\\``, ``%``, and ``_`` for SQL ILIKE with ESCAPE '\\'."""
    return fragment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ilike_contains(fragment: str) -> str:
    return f"%{escape_ilike(fragment.strip())}%"


def normalized_phrase(query: str) -> str:
    """Alphanumeric tokens joined by spaces (punctuation stripped)."""
    return " ".join(_TOKEN_RE.findall(query.casefold()))


def lexical_tokens(query: str) -> list[str]:
    """Content tokens for multi-term matching (order preserved, deduped)."""
    out: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(query.casefold()):
        if len(token) < 2 or token in _LEXICAL_STOPWORDS:
            continue
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def lexical_match_patterns(query: str) -> list[str]:
    """ILIKE patterns for candidate generation (tokens, else normalized phrase)."""
    tokens = lexical_tokens(query)
    if tokens:
        return [ilike_contains(token) for token in tokens]
    phrase = normalized_phrase(query)
    if phrase:
        return [ilike_contains(phrase)]
    stripped = query.strip()
    return [ilike_contains(stripped)] if stripped else []


def score_lexical_relevance(
    query: str, haystacks: list[str | None]
) -> tuple[float, list[str], list[str]]:
    """Return (score, match_reasons, notes) for transparent ranking."""
    reasons: list[str] = []
    notes: list[str] = []
    cleaned = [h.casefold().strip() for h in haystacks if h and h.strip()]
    if not query.strip() or not cleaned:
        return 0.0, reasons, notes

    q = query.casefold().strip()
    phrase = normalized_phrase(query)
    tokens = lexical_tokens(query)
    blob = " ".join(cleaned)

    if any(h == q for h in cleaned) or (phrase and any(h == phrase for h in cleaned)):
        reasons.append("exact_text")
        notes.append("Exact canonical/text match.")
        return 1.0, reasons, notes

    # Strong exact-name boost: a content token equals an entire haystack field.
    exact_token_fields = [h for h in cleaned if h in tokens]
    if exact_token_fields:
        matched = [t for t in tokens if t in blob]
        # Preserve strong boosts for short name queries ("Didac", "Didac Airbus").
        # On longer multi-concept queries, a lone exact name must not dominate
        # denser object-text matches (e.g. legacy start-date descriptions).
        if len(tokens) == 1:
            score = 1.0
            reasons.append("exact_token_name")
        elif len(tokens) == 2 or len(matched) >= max(2, (len(tokens) + 1) // 2):
            score = 0.95
            reasons.append("exact_token_name")
        else:
            coverage = len(matched) / len(tokens)
            score = 0.40 + 0.25 * coverage
            reasons.append("exact_token_name_sparse")
            notes.append(
                f"Exact name for {exact_token_fields[0]!r} but only "
                f"{len(matched)}/{len(tokens)} content tokens overlap "
                "(sparse multi-term match)."
            )
            return score, reasons, notes
        notes.append(
            f"Exact name match for token {exact_token_fields[0]!r} "
            "(preserves strong single-name boosts in multi-term queries)."
        )
        return score, reasons, notes

    if phrase and phrase in blob:
        reasons.append("phrase_contains")
        notes.append("Normalized query phrase contained in searchable text.")
        return 0.85, reasons, notes

    if q in blob:
        reasons.append("query_contains")
        notes.append("Raw query contained in searchable text.")
        return 0.80, reasons, notes

    if not tokens:
        reasons.append("weak_normalized_match")
        notes.append("No content tokens after stopword removal.")
        return 0.25, reasons, notes

    matched = [t for t in tokens if t in blob]
    coverage = len(matched) / len(tokens)
    if not matched:
        reasons.append("candidate_without_token_overlap")
        notes.append("Row matched SQL candidate generation but not token scoring.")
        return 0.15, reasons, notes

    if coverage >= 1.0:
        score = 0.75
        reasons.append("all_tokens_present")
        notes.append("All content tokens present in searchable text.")
    elif len(matched) >= 2:
        score = 0.45 + 0.25 * coverage
        reasons.append("multi_token_partial")
        notes.append(
            f"Matched {len(matched)}/{len(tokens)} content tokens "
            "(partial multi-term overlap)."
        )
    elif len(tokens) == 1:
        score = 0.70
        reasons.append("single_token_match")
        notes.append("Single content-token lexical match.")
    else:
        # One token of a multi-term query: keep as a candidate, but do not dominate.
        score = 0.38
        reasons.append("partial_token_match")
        notes.append(
            f"Matched {len(matched)}/{len(tokens)} content tokens "
            "(avoids OR-token noise dominating rank)."
        )
    return score, reasons, notes
