"""Provider-neutral semantic review adapter for ontology proposals.

Reviewers recommend only. They never write to the database, invoke DDL, or
bypass deterministic gate failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from semantic_memory.config import Settings, get_settings
from semantic_memory.models.enums import GateDecision, ProposalType
from semantic_memory.services.gates import GateOutcome


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    REUSE_EXISTING = "reuse_existing"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class ReviewRequest:
    proposal_type: ProposalType
    payload: dict[str, Any]
    summary: str | None = None


@dataclass(frozen=True)
class ReviewResult:
    decision: ReviewDecision
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SemanticReviewer(Protocol):
    """Replaceable semantic review interface.

    Implementations must be side-effect free with respect to persistence.
    """

    def review(self, request: ReviewRequest) -> ReviewResult:
        """Return a recommendation for the proposal payload."""


class DisabledSemanticReviewer:
    """Default reviewer: never calls an LLM and always defers to manual review."""

    def review(self, request: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            decision=ReviewDecision.MANUAL_REVIEW,
            reason="Semantic review is disabled",
            details={"mode": "disabled", "proposal_type": request.proposal_type.value},
        )


class MockSemanticReviewer:
    """Deterministic test reviewer driven by payload metadata.

    Payload may include ``review_decision`` set to one of the ReviewDecision
    values. Missing or unknown values yield ``manual_review``.
    """

    def review(self, request: ReviewRequest) -> ReviewResult:
        raw = (request.payload.get("metadata") or {}).get("review_decision")
        if raw is None:
            raw = request.payload.get("review_decision")
        try:
            decision = ReviewDecision(str(raw)) if raw is not None else ReviewDecision.MANUAL_REVIEW
        except ValueError:
            decision = ReviewDecision.MANUAL_REVIEW
        return ReviewResult(
            decision=decision,
            reason=f"Mock reviewer decision: {decision.value}",
            details={"mode": "mock", "proposal_type": request.proposal_type.value},
        )


class FailingSemanticReviewer:
    """Test helper that raises to exercise failure degradation."""

    def review(self, request: ReviewRequest) -> ReviewResult:
        raise RuntimeError("semantic reviewer unavailable")


def build_semantic_reviewer(settings: Settings | None = None) -> SemanticReviewer:
    cfg = settings or get_settings()
    if cfg.semantic_review_mode == "mock":
        return MockSemanticReviewer()
    # external mode is reserved; fall back to disabled until a provider is wired.
    return DisabledSemanticReviewer()


def review_decision_to_gate(decision: ReviewDecision) -> GateDecision:
    mapping = {
        ReviewDecision.ACCEPT: GateDecision.PASS,
        ReviewDecision.REJECT: GateDecision.FAIL,
        ReviewDecision.REUSE_EXISTING: GateDecision.REUSE_RECOMMENDED,
        ReviewDecision.MANUAL_REVIEW: GateDecision.MANUAL_REVIEW,
    }
    return mapping[decision]


def make_semantic_review_gate(
    reviewer: SemanticReviewer,
) -> Any:
    """Return an extra-gate callable that never mutates the database."""

    def _gate(proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome | None:
        # Skip advisory review when deterministic gates already fail/reuse.
        # The pipeline invokes extras before final_deterministic, so this gate
        # still runs; ProposalService aggregation prefers fail/reuse over review.
        try:
            result = reviewer.review(ReviewRequest(proposal_type=proposal_type, payload=payload))
        except Exception as exc:  # noqa: BLE001 - degrade to manual_review
            return GateOutcome(
                "semantic_review",
                GateDecision.MANUAL_REVIEW,
                {
                    "reason": "reviewer_failure",
                    "error_type": type(exc).__name__,
                },
            )
        return GateOutcome(
            "semantic_review",
            review_decision_to_gate(result.decision),
            {
                "review_decision": result.decision.value,
                "reason": result.reason,
                **result.details,
            },
        )

    return _gate
