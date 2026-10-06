"""In-process counters for semantic review observability."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class SemanticReviewMetrics:
    semantic_reviews_total: int = 0
    semantic_reviews_approved_total: int = 0
    semantic_reviews_rejected_total: int = 0
    semantic_reviews_manual_total: int = 0
    semantic_review_challenges_total: int = 0
    semantic_review_overturns_total: int = 0
    clarification_requests_total: int = 0
    clarification_resolved_total: int = 0
    clarification_rounds_total: int = 0
    clarification_proposals_with_rounds: int = 0
    semantic_review_input_tokens_total: int = 0
    semantic_review_output_tokens_total: int = 0
    semantic_review_cost_total_usd: float = 0.0
    semantic_review_latency_seconds_total: float = 0.0
    semantic_review_latency_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_review(
        self,
        *,
        decision: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        latency_seconds: float | None = None,
        challenge: bool = False,
        overturn: bool = False,
    ) -> None:
        with self._lock:
            self.semantic_reviews_total += 1
            if decision in {"approve"}:
                self.semantic_reviews_approved_total += 1
            elif decision in {"reject", "reuse_existing", "uphold_rejection"}:
                self.semantic_reviews_rejected_total += 1
            else:
                self.semantic_reviews_manual_total += 1
            if challenge:
                self.semantic_review_challenges_total += 1
            if overturn:
                self.semantic_review_overturns_total += 1
            if input_tokens is not None:
                self.semantic_review_input_tokens_total += input_tokens
            if output_tokens is not None:
                self.semantic_review_output_tokens_total += output_tokens
            if cost_usd is not None:
                self.semantic_review_cost_total_usd += cost_usd
            if latency_seconds is not None:
                self.semantic_review_latency_seconds_total += latency_seconds
                self.semantic_review_latency_count += 1

    def record_clarification_opened(self) -> None:
        with self._lock:
            self.clarification_requests_total += 1

    def record_clarification_resolved(self, *, rounds_for_proposal: int | None = None) -> None:
        with self._lock:
            self.clarification_resolved_total += 1
            if rounds_for_proposal is not None and rounds_for_proposal > 0:
                self.clarification_rounds_total += rounds_for_proposal
                self.clarification_proposals_with_rounds += 1

    @property
    def challenge_overturn_rate(self) -> float | None:
        with self._lock:
            if self.semantic_review_challenges_total == 0:
                return None
            return self.semantic_review_overturns_total / self.semantic_review_challenges_total

    @property
    def clarification_resolution_rate(self) -> float | None:
        with self._lock:
            if self.clarification_requests_total == 0:
                return None
            return self.clarification_resolved_total / self.clarification_requests_total

    @property
    def clarification_rounds_per_proposal(self) -> float | None:
        with self._lock:
            if self.clarification_proposals_with_rounds == 0:
                return None
            return self.clarification_rounds_total / self.clarification_proposals_with_rounds

    def snapshot(self) -> dict[str, float | int | None]:
        with self._lock:
            avg_latency = None
            if self.semantic_review_latency_count:
                avg_latency = (
                    self.semantic_review_latency_seconds_total / self.semantic_review_latency_count
                )
            resolution_rate = (
                None
                if self.clarification_requests_total == 0
                else self.clarification_resolved_total / self.clarification_requests_total
            )
            rounds_per = (
                None
                if self.clarification_proposals_with_rounds == 0
                else self.clarification_rounds_total / self.clarification_proposals_with_rounds
            )
            return {
                "semantic_reviews_total": self.semantic_reviews_total,
                "semantic_reviews_approved_total": self.semantic_reviews_approved_total,
                "semantic_reviews_rejected_total": self.semantic_reviews_rejected_total,
                "semantic_reviews_manual_total": self.semantic_reviews_manual_total,
                "semantic_review_challenges_total": self.semantic_review_challenges_total,
                "semantic_review_overturns_total": self.semantic_review_overturns_total,
                "challenge_overturn_rate": (
                    None
                    if self.semantic_review_challenges_total == 0
                    else self.semantic_review_overturns_total
                    / self.semantic_review_challenges_total
                ),
                "clarification_requests_total": self.clarification_requests_total,
                "clarification_resolved_total": self.clarification_resolved_total,
                "clarification_resolution_rate": resolution_rate,
                "clarification_rounds_per_proposal": rounds_per,
                "semantic_review_input_tokens_total": self.semantic_review_input_tokens_total,
                "semantic_review_output_tokens_total": self.semantic_review_output_tokens_total,
                "semantic_review_cost_total_usd": self.semantic_review_cost_total_usd,
                "semantic_review_latency_seconds_avg": avg_latency,
            }

    def reset(self) -> None:
        with self._lock:
            self.semantic_reviews_total = 0
            self.semantic_reviews_approved_total = 0
            self.semantic_reviews_rejected_total = 0
            self.semantic_reviews_manual_total = 0
            self.semantic_review_challenges_total = 0
            self.semantic_review_overturns_total = 0
            self.clarification_requests_total = 0
            self.clarification_resolved_total = 0
            self.clarification_rounds_total = 0
            self.clarification_proposals_with_rounds = 0
            self.semantic_review_input_tokens_total = 0
            self.semantic_review_output_tokens_total = 0
            self.semantic_review_cost_total_usd = 0.0
            self.semantic_review_latency_seconds_total = 0.0
            self.semantic_review_latency_count = 0


SEMANTIC_REVIEW_METRICS = SemanticReviewMetrics()
