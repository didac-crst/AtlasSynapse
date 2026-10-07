"""Temporal / effective-state intent for retrieval ranking.

Lexical stopwords already drop words like ``current`` / ``previously`` from
candidate generation. This module re-reads the raw query for ranking intent and
scores statement validity bounds + status accordingly.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from semantic_memory.models.enums import StatementStatus

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Phrase cues checked against the casefolded alphanumeric query (not tokenized).
_HISTORICAL_PHRASES = (
    "previously",
    "previous",
    "formerly",
    "former",
    "before",
    "used to",
    "back then",
    "at the time",
    "originally",
    "earlier",
    "in the past",
    "history",
    "historical",
    "superseded",
)

_CURRENT_PHRASES = (
    "currently",
    "current",
    "right now",
    "as of today",
    "nowadays",
    "presently",
    "latest",
    "today",
)

_STRONG_HISTORICAL = frozenset(
    {
        "before",
        "previous",
        "previously",
        "former",
        "formerly",
        "used to",
    }
)


class TemporalIntent(StrEnum):
    CURRENT = "current"
    HISTORICAL = "historical"
    NEUTRAL = "neutral"


def _has_phrase(q: str, spaced: str, phrase: str) -> bool:
    if " " in phrase:
        return phrase in q
    return f" {phrase} " in spaced


def detect_temporal_intent(query: str | None) -> TemporalIntent:
    """Classify query temporal intent for ranking (not a truth judgment)."""
    if not query or not query.strip():
        return TemporalIntent.NEUTRAL
    q = " ".join(_TOKEN_RE.findall(query.casefold()))
    spaced = f" {q} "
    historical = any(_has_phrase(q, spaced, phrase) for phrase in _HISTORICAL_PHRASES)
    current = any(_has_phrase(q, spaced, phrase) for phrase in _CURRENT_PHRASES)

    # Soft past-tense cue when no current cue (e.g. "when was Didac at INPG",
    # "Didac studied").
    soft_past = (not current) and (
        " was " in spaced
        or spaced.startswith("was ")
        or " were " in spaced
        or " studied " in spaced
        or spaced.endswith(" studied")
        or spaced.startswith("studied ")
    )

    if historical and current:
        # "before this current role" → historical; bare "current" wins otherwise.
        if any(_has_phrase(q, spaced, cue) for cue in _STRONG_HISTORICAL):
            return TemporalIntent.HISTORICAL
        return TemporalIntent.CURRENT
    if historical or soft_past:
        return TemporalIntent.HISTORICAL
    if current:
        return TemporalIntent.CURRENT
    return TemporalIntent.NEUTRAL


def score_temporal_validity(
    *,
    valid_from: datetime | None,
    valid_to: datetime | None,
    status: str,
    now: datetime,
    intent: TemporalIntent,
) -> tuple[float, list[str], list[str]]:
    """Return (score, reasons, notes) for transparent temporal ranking."""
    reasons: list[str] = []
    notes: list[str] = []
    unbounded = valid_from is None and valid_to is None
    future = valid_from is not None and now < valid_from
    ended = valid_to is not None and now > valid_to
    open_end = valid_from is not None and valid_to is None and not future
    in_force = (not unbounded) and (not future) and (not ended)
    superseded = status == "superseded"
    retracted = status == "retracted"

    if retracted:
        reasons.append("retracted_demoted")
        notes.append("Retracted statements are strongly demoted for ordinary search.")
        return 0.02, reasons, notes

    if intent == TemporalIntent.CURRENT:
        if superseded:
            reasons.append("superseded_demoted_for_current")
            notes.append("Current intent demotes superseded statements.")
            return 0.05, reasons, notes
        if open_end or (in_force and valid_to is None):
            reasons.append("effective_open_end")
            notes.append(
                "Current intent prefers open-ended effective facts "
                "(valid_from set, valid_to null, as_of in range)."
            )
            return 1.0, reasons, notes
        if in_force:
            reasons.append("effective_bounded")
            notes.append("as_of falls inside valid_from/valid_to.")
            return 0.95, reasons, notes
        if unbounded:
            reasons.append("unbounded_demoted_for_current")
            notes.append(
                "Temporally unspecified (null/null) descriptive facts are demoted "
                "vs structured effective statements for current intent."
            )
            return 0.32, reasons, notes
        if ended:
            reasons.append("ended_demoted_for_current")
            notes.append("Ended validity intervals are demoted for current intent.")
            return 0.12, reasons, notes
        if future:
            reasons.append("future_demoted")
            notes.append("Future-dated validity is demoted.")
            return 0.15, reasons, notes
        return 0.4, reasons, notes

    if intent == TemporalIntent.HISTORICAL:
        if superseded:
            reasons.append("superseded_boosted_for_historical")
            notes.append("Historical intent promotes superseded prior beliefs.")
            return 1.0, reasons, notes
        if ended:
            reasons.append("ended_boosted_for_historical")
            notes.append("Ended intervals are preferred for historical intent.")
            return 0.95, reasons, notes
        if unbounded:
            reasons.append("unbounded_historical_candidate")
            notes.append(
                "Unbounded descriptive duplicates may carry prior beliefs for historical queries."
            )
            return 0.88, reasons, notes
        if open_end or in_force:
            reasons.append("effective_demoted_for_historical")
            notes.append("Currently effective facts are demoted for historical intent.")
            return 0.40, reasons, notes
        if future:
            return 0.2, ["future_demoted"], ["Future-dated validity is demoted."]
        return 0.5, reasons, notes

    # Neutral default search: mild preference for effective structured truth.
    if superseded:
        reasons.append("superseded_demoted")
        notes.append("Default search demotes superseded statements.")
        return 0.08, reasons, notes
    if open_end or in_force:
        reasons.append("effective_preferred")
        notes.append("Default search prefers temporally effective statements.")
        return 1.0 if open_end else 0.92, reasons, notes
    if unbounded:
        reasons.append("unbounded_soft_demoted")
        notes.append(
            "null/null valid_from/valid_to means temporally unspecified "
            "(not 'valid forever'); soft-demoted vs effective facts."
        )
        return 0.48, reasons, notes
    if ended:
        reasons.append("ended_soft_demoted")
        notes.append("Ended intervals are soft-demoted for default search.")
        return 0.28, reasons, notes
    if future:
        reasons.append("future_demoted")
        notes.append("Future-dated validity is demoted.")
        return 0.2, reasons, notes
    return 0.5, reasons, notes


def statement_status_for_intent(intent: TemporalIntent) -> StatementStatus | None:
    """Status filter for statement search under a temporal intent.

    Historical queries include superseded/retracted rows (ranked separately);
    default and current intents stay asserted-only.
    """
    if intent == TemporalIntent.HISTORICAL:
        return None
    return StatementStatus.ASSERTED
