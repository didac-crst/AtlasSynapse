"""Provider-neutral semantic review adapter for ontology proposals.

Reviewers recommend only. They never write to the database, invoke DDL, or
bypass deterministic gate failures. Every review call is logged via
``LlmCallLogger`` when a logger is configured.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from semantic_memory.config import Settings, get_settings
from semantic_memory.models.enums import GateDecision, LlmCallStatus, ProposalType
from semantic_memory.services.gates import GateOutcome
from semantic_memory.services.llm_logging import (
    LlmCallCompletion,
    LlmCallContext,
    LlmCallLogger,
    NoOpLlmCallLogger,
)


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
    actor_id: uuid.UUID | None = None
    operation_log_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    trace_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ReviewResult:
    decision: ReviewDecision
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SemanticReviewer(Protocol):
    """Replaceable semantic review interface.

    Implementations must be side-effect free with respect to knowledge/ontology
    persistence. Observability logging is performed by wrappers.
    """

    def review(self, request: ReviewRequest) -> ReviewResult:
        """Return a recommendation for the proposal payload."""


class DisabledSemanticReviewer:
    """Default reviewer: never calls an LLM and always defers to manual review."""

    provider = "disabled"
    model = "none"

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

    provider = "mock"
    model = "mock-reviewer"

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

    provider = "mock"
    model = "failing-reviewer"

    def review(self, request: ReviewRequest) -> ReviewResult:
        raise RuntimeError("semantic reviewer unavailable")


class LoggingSemanticReviewer:
    """Wrap a reviewer so every call is recorded in ``llm_call_log``."""

    def __init__(
        self,
        inner: SemanticReviewer,
        logger: LlmCallLogger,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self._inner = inner
        self._logger = logger
        self._provider = str(provider or getattr(inner, "provider", "unknown"))
        self._model = str(model or getattr(inner, "model", "unknown"))

    def review(self, request: ReviewRequest) -> ReviewResult:
        call = self._logger.begin(
            LlmCallContext(
                purpose="semantic_review",
                provider=self._provider,
                model=self._model,
                actor_id=request.actor_id,
                operation_log_id=request.operation_log_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                reason="ontology_proposal_review",
                metadata={
                    "proposal_type": request.proposal_type.value,
                    # Never persist raw proposal payloads by default.
                    "payload_keys": sorted(str(key) for key in request.payload),
                },
            )
        )
        try:
            result = self._inner.review(request)
        except Exception as exc:
            self._logger.complete(
                call,
                LlmCallCompletion(
                    status=LlmCallStatus.UNAVAILABLE,
                    outcome="unavailable",
                    reason="reviewer_failure",
                    error_code="DEPENDENCY_UNAVAILABLE",
                    error_message=str(exc),
                    metadata={"error_type": type(exc).__name__},
                ),
            )
            raise

        status = _status_for_decision(result.decision, provider=self._provider)
        input_tokens, output_tokens = _token_estimate(self._provider, request, result)
        self._logger.complete(
            call,
            LlmCallCompletion(
                status=status,
                outcome=result.decision.value,
                reason=result.reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                metadata={"review_details": result.details},
            ),
        )
        return result


def _status_for_decision(decision: ReviewDecision, *, provider: str) -> LlmCallStatus:
    if provider == "disabled":
        return LlmCallStatus.MANUAL_REVIEW
    if decision == ReviewDecision.MANUAL_REVIEW and provider == "disabled":
        return LlmCallStatus.MANUAL_REVIEW
    return LlmCallStatus.SUCCEEDED


def _token_estimate(
    provider: str, request: ReviewRequest, result: ReviewResult
) -> tuple[int | None, int | None]:
    """Mock reviewers report deterministic token counts; disabled reports none."""
    if provider == "disabled":
        return None, None
    if provider != "mock":
        return None, None
    # Cheap deterministic stand-in for provider usage accounting in tests.
    payload_chars = len(str(request.payload))
    reason_chars = len(result.reason)
    return max(1, payload_chars // 4), max(1, reason_chars // 4)


def build_semantic_reviewer(
    settings: Settings | None = None,
    *,
    logger: LlmCallLogger | None = None,
) -> SemanticReviewer:
    cfg = settings or get_settings()
    if cfg.semantic_review_mode == "mock":
        inner: SemanticReviewer = MockSemanticReviewer()
    else:
        # external mode is reserved; fall back to disabled until a provider is wired.
        inner = DisabledSemanticReviewer()
    return LoggingSemanticReviewer(inner, logger or NoOpLlmCallLogger())


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
    *,
    context_provider: Any | None = None,
) -> Any:
    """Return an extra-gate callable that never mutates the database.

    ``context_provider`` is an optional zero-arg callable returning a dict with
    actor_id / operation_log_id / request_id / trace_id for LLM logging.
    """

    def _gate(proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome | None:
        context = context_provider() if context_provider is not None else {}
        try:
            result = reviewer.review(
                ReviewRequest(
                    proposal_type=proposal_type,
                    payload=payload,
                    actor_id=context.get("actor_id"),
                    operation_log_id=context.get("operation_log_id"),
                    request_id=context.get("request_id"),
                    trace_id=context.get("trace_id"),
                )
            )
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
