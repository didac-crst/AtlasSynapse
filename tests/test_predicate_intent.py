"""Soft predicate intent unit tests."""

from __future__ import annotations

import uuid

from semantic_memory.services.predicate_intent import (
    PredicateLexicon,
    score_predicate_intent,
    top_intent_predicates,
)


def _lexicon() -> PredicateLexicon:
    tokens = {
        "holdsRole": frozenset({"holds", "role", "assignment", "agent"}),
        "roleAt": frozenset({"role", "at", "organization", "context"}),
        "hasGoal": frozenset({"has", "goal", "objective", "durable"}),
        "studiedAt": frozenset({"studied", "at", "organization", "person", "institution"}),
        "employedBy": frozenset({"employed", "by", "organization", "work"}),
    }
    counts: dict[str, int] = {}
    for cues in tokens.values():
        for cue in cues:
            counts[cue] = counts.get(cue, 0) + 1
            # plurals lightly
            counts[f"{cue}s"] = counts.get(f"{cue}s", 0) + 1
    distinctive = frozenset(t for t, n in counts.items() if n <= 1)
    return PredicateLexicon(
        tokens_by_key=tokens,
        key_by_id={uuid.uuid4(): "holdsRole"},
        distinctive_tokens=distinctive,
    )


def test_role_query_boosts_holds_role_not_has_goal() -> None:
    lex = _lexicon()
    role_boost, role_reasons, _ = score_predicate_intent(
        "Didac previous Airbus role", "holdsRole", lex
    )
    goal_boost, _, _ = score_predicate_intent(
        "Didac previous Airbus role", "hasGoal", lex
    )
    assert role_boost > 0
    assert goal_boost == 0
    assert "predicate_intent_nl_cue" in role_reasons


def test_job_nl_cue_boosts_holds_role() -> None:
    lex = _lexicon()
    boost, reasons, _ = score_predicate_intent("Didac job", "holdsRole", lex)
    assert boost >= 0.7
    assert "predicate_intent_nl_cue" in reasons


def test_study_query_selects_studied_at_intent() -> None:
    lex = _lexicon()
    keys = top_intent_predicates("Didac studied", lex)
    assert keys[0] == "studiedAt"
    study_boost, _, _ = score_predicate_intent("Didac studied", "studiedAt", lex)
    role_boost, _, _ = score_predicate_intent("Didac studied", "holdsRole", lex)
    assert study_boost > role_boost
