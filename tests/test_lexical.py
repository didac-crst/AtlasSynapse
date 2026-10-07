"""Unit tests for tokenized lexical matching helpers."""

from __future__ import annotations

from semantic_memory.services.lexical import (
    lexical_match_patterns,
    lexical_tokens,
    normalized_phrase,
    score_lexical_relevance,
)


def test_punctuation_insensitive_tokenization() -> None:
    assert lexical_tokens("Who is Didac?") == ["didac"]
    assert normalized_phrase("Who is Didac?") == "who is didac"
    assert lexical_tokens("Didac Airbus") == ["didac", "airbus"]
    assert lexical_tokens("Didac current Airbus role") == ["didac", "airbus", "role"]


def test_match_patterns_are_token_ilikes_not_full_string() -> None:
    patterns = lexical_match_patterns("Didac Airbus")
    assert patterns == ["%didac%", "%airbus%"]
    assert lexical_match_patterns("Who is Didac?") == ["%didac%"]


def test_exact_name_keeps_strong_boost_in_multi_term_query() -> None:
    single, single_reasons, _ = score_lexical_relevance("Didac", ["Didac"])
    assert single == 1.0
    assert "exact_text" in single_reasons

    # Multi-term query where one entity name equals a single content token.
    score, reasons, _ = score_lexical_relevance("Didac Airbus", ["Didac"])
    assert score == 0.95
    assert "exact_token_name" in reasons

    score_airbus, _, _ = score_lexical_relevance("Didac Airbus", ["Airbus"])
    assert score_airbus == 0.95


def test_partial_or_token_does_not_dominate() -> None:
    # Only one of three content tokens appears in a long descriptive blob.
    score, reasons, _ = score_lexical_relevance(
        "Didac current Airbus role",
        ["Some unrelated note about a role somewhere"],
    )
    assert score < 0.5
    assert "partial_token_match" in reasons


def test_sparse_exact_name_does_not_dominate_long_queries() -> None:
    sparse, reasons, _ = score_lexical_relevance(
        "AtlasSynapse previously Airbus role start date",
        ["Airbus"],
    )
    dense, _, _ = score_lexical_relevance(
        "AtlasSynapse previously Airbus role start date",
        [
            "Quality Engineering End-to-End Analytics Manager at Airbus in "
            "Toulouse, started 1 January 2026"
        ],
    )
    assert "exact_token_name_sparse" in reasons
    assert dense > sparse


def test_previously_is_not_a_content_token() -> None:
    assert "previously" not in lexical_tokens(
        "What did AtlasSynapse previously think the Airbus role start date was?"
    )


def test_all_tokens_in_text_scores_above_partial() -> None:
    all_score, all_reasons, _ = score_lexical_relevance(
        "Airbus role",
        ["Quality Engineering role at Airbus in Toulouse"],
    )
    partial_score, _, _ = score_lexical_relevance(
        "Airbus role",
        ["Something about a role only"],
    )
    assert all_score > partial_score
    assert "all_tokens_present" in all_reasons
