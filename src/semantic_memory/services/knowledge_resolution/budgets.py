"""Budget guards for knowledge-ingestion resolution agenda."""

from __future__ import annotations

import time
from typing import Any


class ResolutionBudgets:
    """Track candidate attempts, LLM calls, ontology proposals, and wall time."""

    def __init__(self, budgets_json: dict[str, Any] | None) -> None:
        raw = budgets_json if isinstance(budgets_json, dict) else {}
        self.max_candidate_attempts = _optional_int(raw.get("max_candidate_attempts"))
        self.max_llm_calls = _optional_int(raw.get("max_llm_calls"))
        self.max_ontology_proposals = _optional_int(raw.get("max_ontology_proposals"))
        self.max_wall_ms = _optional_int(raw.get("max_wall_ms"))
        self.candidate_attempts = 0
        self.llm_calls = 0
        self.ontology_proposals = 0
        self._started = time.monotonic()
        self.exhausted = False
        self.exhausted_reason: str | None = None

    def wall_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def can_attempt_candidate(self) -> bool:
        if self.exhausted:
            return False
        if self.max_wall_ms is not None and self.wall_ms() >= self.max_wall_ms:
            self._mark("wall_time")
            return False
        if (
            self.max_candidate_attempts is not None
            and self.candidate_attempts >= self.max_candidate_attempts
        ):
            self._mark("candidate_attempts")
            return False
        return True

    def record_candidate_attempt(self) -> None:
        self.candidate_attempts += 1
        if (
            self.max_candidate_attempts is not None
            and self.candidate_attempts >= self.max_candidate_attempts
        ):
            self._mark("candidate_attempts")

    def can_propose_ontology(self) -> bool:
        if self.exhausted:
            return False
        if (
            self.max_ontology_proposals is not None
            and self.ontology_proposals >= self.max_ontology_proposals
        ):
            self._mark("ontology_proposals")
            return False
        return True

    def remaining_llm_ok(self) -> bool:
        """True if another LLM call is still within budget (does not mark exhausted)."""
        if self.exhausted:
            return False
        if self.max_llm_calls is None:
            return True
        return self.llm_calls < self.max_llm_calls

    def record_ontology_proposal(self) -> None:
        self.ontology_proposals += 1
        if (
            self.max_ontology_proposals is not None
            and self.ontology_proposals >= self.max_ontology_proposals
        ):
            self._mark("ontology_proposals")

    def record_llm_call(self, n: int = 1) -> None:
        self.llm_calls += n
        if self.max_llm_calls is not None and self.llm_calls >= self.max_llm_calls:
            self._mark("llm_calls")

    def check_wall(self) -> bool:
        if self.max_wall_ms is not None and self.wall_ms() >= self.max_wall_ms:
            self._mark("wall_time")
            return False
        return not self.exhausted

    def _mark(self, reason: str) -> None:
        self.exhausted = True
        self.exhausted_reason = reason


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
