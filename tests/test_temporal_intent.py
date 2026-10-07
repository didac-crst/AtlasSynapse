"""Tests for temporal / effective-state retrieval intent."""

from __future__ import annotations

from datetime import UTC, datetime

from semantic_memory.models.enums import StatementStatus
from semantic_memory.services.temporal_intent import (
    TemporalIntent,
    detect_temporal_intent,
    score_temporal_validity,
    statement_status_for_intent,
)


def test_detect_current_and_historical_cues() -> None:
    assert detect_temporal_intent("Didac current Airbus role") == TemporalIntent.CURRENT
    assert (
        detect_temporal_intent(
            "What did AtlasSynapse previously think the Airbus role start date was?"
        )
        == TemporalIntent.HISTORICAL
    )
    assert (
        detect_temporal_intent("What was Didac doing before this role?")
        == TemporalIntent.HISTORICAL
    )
    assert detect_temporal_intent("When was Didac at INPG?") == TemporalIntent.HISTORICAL
    assert detect_temporal_intent("Didac Airbus") == TemporalIntent.NEUTRAL
    assert detect_temporal_intent("Didac studied") == TemporalIntent.HISTORICAL


def test_current_beats_soft_past_when_current_present() -> None:
    assert (
        detect_temporal_intent("When did Didac start his current role?")
        == TemporalIntent.CURRENT
    )


def test_before_current_is_historical() -> None:
    assert (
        detect_temporal_intent("What was Didac doing before this current role?")
        == TemporalIntent.HISTORICAL
    )


def test_effective_open_end_outranks_unbounded_for_current() -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    effective, eff_reasons, _ = score_temporal_validity(
        valid_from=datetime(2025, 9, 1, tzinfo=UTC),
        valid_to=None,
        status="asserted",
        now=now,
        intent=TemporalIntent.CURRENT,
    )
    legacy, leg_reasons, _ = score_temporal_validity(
        valid_from=None,
        valid_to=None,
        status="asserted",
        now=now,
        intent=TemporalIntent.CURRENT,
    )
    assert effective > legacy
    assert "effective_open_end" in eff_reasons
    assert "unbounded_demoted_for_current" in leg_reasons


def test_historical_promotes_superseded_and_legacy() -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    superseded, ss_reasons, _ = score_temporal_validity(
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=None,
        status="superseded",
        now=now,
        intent=TemporalIntent.HISTORICAL,
    )
    legacy, leg_reasons, _ = score_temporal_validity(
        valid_from=None,
        valid_to=None,
        status="asserted",
        now=now,
        intent=TemporalIntent.HISTORICAL,
    )
    current_role, cur_reasons, _ = score_temporal_validity(
        valid_from=datetime(2025, 9, 1, tzinfo=UTC),
        valid_to=None,
        status="asserted",
        now=now,
        intent=TemporalIntent.HISTORICAL,
    )
    assert superseded >= legacy > current_role
    assert "superseded_boosted_for_historical" in ss_reasons
    assert "unbounded_historical_candidate" in leg_reasons
    assert "effective_demoted_for_historical" in cur_reasons


def test_statement_status_filter_by_intent() -> None:
    assert statement_status_for_intent(TemporalIntent.CURRENT) == StatementStatus.ASSERTED
    assert statement_status_for_intent(TemporalIntent.NEUTRAL) == StatementStatus.ASSERTED
    assert statement_status_for_intent(TemporalIntent.HISTORICAL) is None
