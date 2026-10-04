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
from semantic_memory.schemas.semantic_review import (
    RelatedExistingConcept,
    ReviewReason,
    SemanticDecision,
    StructuredReviewResult,
)
from semantic_memory.services.gates import GateOutcome
from semantic_memory.services.llm_logging import (
    LlmCallCompletion,
    LlmCallContext,
    LlmCallLogger,
    NoOpLlmCallLogger,
)
from semantic_memory.services.openai_reviewer import (
    PROMPT_TEMPLATE_VERSION,
    OpenAISemanticReviewer,
)
from semantic_memory.services.review_context import ReviewContext
from semantic_memory.services.review_metrics import SEMANTIC_REVIEW_METRICS


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REUSE_EXISTING = "reuse_existing"
    MANUAL_REVIEW = "manual_review"
    UPHOLD_REJECTION = "uphold_rejection"

    @classmethod
    def from_raw(cls, raw: Any) -> ReviewDecision:
        text = str(raw)
        if text == "accept":  # backward-compatible mock metadata
            return cls.APPROVE
        return cls(text)


@dataclass(frozen=True)
class ReviewRequest:
    proposal_type: ProposalType
    payload: dict[str, Any]
    summary: str | None = None
    actor_id: uuid.UUID | None = None
    operation_log_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    trace_id: uuid.UUID | None = None
    context: ReviewContext | None = None
    prior_decision: StructuredReviewResult | None = None
    challenge_reason: str | None = None
    evidence_refs: list[Any] = field(default_factory=list)
    proposed_revision: dict[str, Any] | None = None
    review_stage: str = "initial"


@dataclass(frozen=True)
class ReviewResult:
    decision: ReviewDecision
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    structured: StructuredReviewResult | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION


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
        structured = StructuredReviewResult(
            decision=SemanticDecision.MANUAL_REVIEW,
            confidence=0.0,
            summary="Semantic review is disabled",
            reasons=[
                ReviewReason(code="semantic_review_disabled", message="Semantic review is disabled")
            ],
            context_sufficient=True,
            challengeable=False,
        )
        return ReviewResult(
            decision=ReviewDecision.MANUAL_REVIEW,
            reason=structured.summary,
            details={"mode": "disabled", "proposal_type": request.proposal_type.value},
            structured=structured,
        )


class MockSemanticReviewer:
    """Deterministic test reviewer driven by payload metadata.

    Payload may include ``review_decision`` / ``review_result`` under metadata.
    Missing or unknown values yield ``manual_review``.
    """

    provider = "mock"
    model = "mock-reviewer"

    def review(self, request: ReviewRequest) -> ReviewResult:
        metadata = request.payload.get("metadata") or {}
        raw_result = metadata.get("review_result")
        if isinstance(raw_result, dict):
            structured = StructuredReviewResult.model_validate(raw_result)
            decision = ReviewDecision(structured.decision.value)
            return ReviewResult(
                decision=decision,
                reason=structured.summary,
                details={"mode": "mock", "proposal_type": request.proposal_type.value},
                structured=structured,
            )

        raw = metadata.get("review_decision")
        if raw is None:
            raw = request.payload.get("review_decision")
        try:
            decision = (
                ReviewDecision.from_raw(raw) if raw is not None else ReviewDecision.MANUAL_REVIEW
            )
        except ValueError:
            decision = ReviewDecision.MANUAL_REVIEW

        # Challenge-aware mock: optional metadata.challenge_decision
        if request.challenge_reason is not None:
            challenge_raw = metadata.get("challenge_decision")
            if challenge_raw is not None:
                try:
                    decision = ReviewDecision.from_raw(challenge_raw)
                except ValueError:
                    decision = ReviewDecision.MANUAL_REVIEW

        context_sufficient = bool(metadata.get("context_sufficient", True))
        confidence = float(metadata.get("review_confidence", 0.9))
        related = [
            RelatedExistingConcept.model_validate(item)
            for item in metadata.get("related_existing_concepts") or []
        ]
        reasons = [
            ReviewReason.model_validate(item)
            for item in metadata.get("review_reasons")
            or [{"code": f"mock_{decision.value}", "message": f"Mock reviewer: {decision.value}"}]
        ]
        semantic_decision = SemanticDecision(decision.value)
        structured = StructuredReviewResult(
            decision=semantic_decision,
            confidence=confidence,
            summary=f"Mock reviewer decision: {decision.value}",
            reasons=reasons,
            related_existing_concepts=related,
            recommended_actions=[str(x) for x in metadata.get("recommended_actions") or []],
            required_clarification=[
                str(x) for x in metadata.get("required_clarification") or []
            ],
            context_sufficient=context_sufficient,
            challengeable=decision
            in {ReviewDecision.REJECT, ReviewDecision.REUSE_EXISTING, ReviewDecision.MANUAL_REVIEW},
        )
        proposal_key = str(
            request.payload.get("key")
            or request.payload.get("alias")
            or request.payload.get("child_key")
            or "the proposal"
        )
        structured = ensure_clarification_asks(structured, proposal_key=proposal_key)
        return ReviewResult(
            decision=decision,
            reason=structured.summary,
            details={"mode": "mock", "proposal_type": request.proposal_type.value},
            structured=structured,
        )


class FailingSemanticReviewer:
    """Test helper that raises to exercise failure degradation."""

    provider = "mock"
    model = "failing-reviewer"

    def review(self, request: ReviewRequest) -> ReviewResult:
        raise RuntimeError("semantic reviewer unavailable")


class ExternalSemanticReviewer:
    """OpenAI Terra reviewer wired through the shared context protocol."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._inner = OpenAISemanticReviewer(settings)
        self.provider = self._inner.provider
        self.model = self._inner.model
        self.model_version = self._inner.model_version

    def review(self, request: ReviewRequest) -> ReviewResult:
        if request.context is None:
            raise RuntimeError("semantic_context_missing")
        if request.context.budget_exceeded:
            structured = StructuredReviewResult(
                decision=SemanticDecision.MANUAL_REVIEW,
                confidence=0.0,
                summary="Semantic context budget exceeded",
                reasons=[
                    ReviewReason(
                        code="semantic_context_budget_exceeded",
                        message="Context could not be packed under the configured token budget",
                    )
                ],
                context_sufficient=False,
                challengeable=True,
            )
            return ReviewResult(
                decision=ReviewDecision.MANUAL_REVIEW,
                reason=structured.summary,
                details={"reason": "semantic_context_budget_exceeded"},
                structured=structured,
            )
        structured, meta = self._inner.review_with_context(
            context=request.context,
            prior_decision=request.prior_decision,
            challenge_reason=request.challenge_reason,
            evidence_refs=request.evidence_refs,
            proposed_revision=request.proposed_revision,
        )
        return ReviewResult(
            decision=ReviewDecision(structured.decision.value),
            reason=structured.summary,
            details={"mode": "external", "provider_meta": {k: meta.get(k) for k in meta}},
            structured=structured,
            input_tokens=meta.get("input_tokens"),
            output_tokens=meta.get("output_tokens"),
            prompt_template_version=str(
                meta.get("prompt_template_version") or PROMPT_TEMPLATE_VERSION
            ),
        )


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

    @property
    def inner(self) -> SemanticReviewer:
        return self._inner

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
                    "review_stage": request.review_stage,
                    # Never persist raw proposal payloads by default.
                    "payload_keys": sorted(str(key) for key in request.payload),
                    "context_keys": (
                        [] if request.context is None else request.context.concept_keys()
                    ),
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
                    error_message="Semantic reviewer call failed",
                    metadata={"error_type": type(exc).__name__},
                ),
            )
            raise

        status = _status_for_decision(result.decision, provider=self._provider)
        input_tokens, output_tokens = _token_estimate(self._provider, request, result)
        if result.input_tokens is not None:
            input_tokens = result.input_tokens
        if result.output_tokens is not None:
            output_tokens = result.output_tokens
        self._logger.complete(
            call,
            LlmCallCompletion(
                status=status,
                outcome=result.decision.value,
                reason=result.reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                metadata={
                    "review_details": result.details,
                    "structured": None
                    if result.structured is None
                    else result.structured.model_dump(mode="json"),
                },
            ),
        )
        # Attach call id for provenance if logger returned a row id.
        call_id = getattr(call, "id", None)
        details = dict(result.details)
        if call_id is not None:
            details["llm_call_log_id"] = str(call_id)
        return ReviewResult(
            decision=result.decision,
            reason=result.reason,
            details=details,
            structured=result.structured,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_template_version=result.prompt_template_version,
        )


def default_clarification_asks(
    *,
    proposal_key: str,
    related: list[RelatedExistingConcept],
) -> list[str]:
    """Structured questions for proposing agents when distinction is ambiguous."""
    key = proposal_key or "the proposal"
    asks: list[str] = []
    for concept in related[:2]:
        asks.append(f"Explain how {key} differs from {concept.key}.")
        asks.append(
            f"Provide an example assertion that should be valid for {key} "
            f"but not for {concept.key}."
        )
    asks.append(
        f"State whether {key} represents an ability, a domain/category, "
        "a relation, or something else."
    )
    return asks[:5]


def description_admits_competing_models(description: str) -> bool:
    """True when wording is vague enough to admit multiple non-equivalent models."""
    text = (description or "").casefold()
    if not text.strip():
        return False
    markers = (
        "some kind of",
        "some sort of",
        "kind of ability or",
        "or focus area",
        "related to agents",
        "related to an agent",
    )
    return any(marker in text for marker in markers)


def ensure_clarification_asks(
    structured: StructuredReviewResult,
    *,
    proposal_key: str,
) -> StructuredReviewResult:
    """Ensure ambiguous manual_review carries proposer-facing clarification asks."""
    if structured.decision != SemanticDecision.MANUAL_REVIEW:
        return structured
    if not structured.related_existing_concepts:
        return structured

    from semantic_memory.schemas.semantic_review import CLARIFICATION_REASON_CODE

    reasons = list(structured.reasons)
    codes = {item.code for item in reasons}
    ambiguity_codes = {
        CLARIFICATION_REASON_CODE,
        "ambiguous_semantic_distinction",
        "semantic_distinction_unclear",
    }
    if not codes & ambiguity_codes:
        related_keys = ", ".join(c.key for c in structured.related_existing_concepts[:3])
        reasons.append(
            ReviewReason(
                code=CLARIFICATION_REASON_CODE,
                message=(
                    "Proposal may overlap existing concept(s) "
                    f"({related_keys}); intended distinction is unclear."
                ),
            )
        )

    asks = [str(x).strip() for x in structured.required_clarification if str(x).strip()]
    if not asks:
        asks = default_clarification_asks(
            proposal_key=proposal_key,
            related=structured.related_existing_concepts,
        )

    actions = list(structured.recommended_actions)
    clarify_action = (
        "Reply via answer_semantic_clarification using clarification_request_id"
    )
    if clarify_action not in actions:
        actions = [clarify_action, *actions]

    return structured.model_copy(
        update={
            "reasons": reasons,
            "required_clarification": asks,
            "recommended_actions": actions,
            "challengeable": True,
        }
    )


def apply_confidence_policy(
    structured: StructuredReviewResult,
    *,
    settings: Settings,
    challenge: bool = False,
    proposal_key: str = "",
    proposal_description: str = "",
) -> StructuredReviewResult:
    """Map model output through application-side confidence / sufficiency policy."""
    from semantic_memory.schemas.semantic_review import CLARIFICATION_REASON_CODE

    decision = structured.decision
    if not structured.context_sufficient:
        structured = structured.model_copy(
            update={
                "decision": SemanticDecision.MANUAL_REVIEW,
                "challengeable": True,
                "reasons": list(structured.reasons)
                + [
                    ReviewReason(
                        code="context_insufficient",
                        message="Reviewer reported insufficient ontology context",
                    )
                ],
            }
        )
        return ensure_clarification_asks(structured, proposal_key=proposal_key)

    approve_threshold = settings.semantic_review_approve_threshold
    reject_threshold = settings.semantic_review_reject_threshold
    confidence = structured.confidence

    if decision == SemanticDecision.APPROVE and confidence < approve_threshold:
        structured = structured.model_copy(
            update={
                "decision": SemanticDecision.MANUAL_REVIEW,
                "reasons": list(structured.reasons)
                + [
                    ReviewReason(
                        code="approve_below_threshold",
                        message=(
                            f"Approve confidence {confidence} below threshold {approve_threshold}"
                        ),
                    )
                ],
            }
        )
    elif decision in {
        SemanticDecision.REJECT,
        SemanticDecision.REUSE_EXISTING,
        SemanticDecision.UPHOLD_REJECTION,
    } and confidence < reject_threshold:
        # Conservative: ambiguity routes to manual review (severe issues still may reject
        # below threshold only when the model explicitly marked context sufficient and
        # confidence is high — here we force manual_review).
        structured = structured.model_copy(
            update={
                "decision": SemanticDecision.MANUAL_REVIEW,
                "reasons": list(structured.reasons)
                + [
                    ReviewReason(
                        code="reject_below_threshold",
                        message=(
                            f"Reject confidence {confidence} below threshold {reject_threshold}"
                        ),
                    )
                ],
            }
        )
    elif (
        not challenge
        and decision in {SemanticDecision.REJECT, SemanticDecision.REUSE_EXISTING}
        and structured.related_existing_concepts
        and description_admits_competing_models(proposal_description)
    ):
        # AtlasSynapse policy: vague multi-model wording must clarify, not force reuse.
        related_keys = ", ".join(c.key for c in structured.related_existing_concepts[:3])
        structured = structured.model_copy(
            update={
                "decision": SemanticDecision.MANUAL_REVIEW,
                "reasons": list(structured.reasons)
                + [
                    ReviewReason(
                        code=CLARIFICATION_REASON_CODE,
                        message=(
                            "Proposal wording admits competing non-equivalent models "
                            f"relative to {related_keys}; clarification required."
                        ),
                    )
                ],
                "summary": (
                    f"The proposal overlaps with {related_keys}, but the intended "
                    "distinction is unclear from the supplied wording."
                ),
            }
        )

    if challenge and structured.decision == SemanticDecision.REJECT:
        # Normalize challenge reject wording to uphold_rejection.
        structured = structured.model_copy(update={"decision": SemanticDecision.UPHOLD_REJECTION})

    # Negative / uncertain outcomes remain challengeable regardless of model flag.
    if structured.decision in {
        SemanticDecision.REJECT,
        SemanticDecision.REUSE_EXISTING,
        SemanticDecision.MANUAL_REVIEW,
        SemanticDecision.UPHOLD_REJECTION,
    }:
        structured = structured.model_copy(update={"challengeable": True})

    return ensure_clarification_asks(structured, proposal_key=proposal_key)


def build_semantic_reviewer(
    settings: Settings | None = None,
    *,
    logger: LlmCallLogger | None = None,
) -> SemanticReviewer:
    cfg = settings or get_settings()
    if cfg.semantic_review_mode == "mock":
        inner: SemanticReviewer = MockSemanticReviewer()
    elif cfg.semantic_review_mode in {"shadow", "external"}:
        # Shadow uses the real provider but proposal outcomes stay non-authoritative.
        if not cfg.semantic_review_api_key:
            inner = DisabledSemanticReviewer()
        else:
            inner = ExternalSemanticReviewer(cfg)
    else:
        inner = DisabledSemanticReviewer()
    return LoggingSemanticReviewer(inner, logger or NoOpLlmCallLogger())


def review_decision_to_gate(decision: ReviewDecision) -> GateDecision:
    mapping = {
        ReviewDecision.APPROVE: GateDecision.PASS,
        ReviewDecision.REJECT: GateDecision.FAIL,
        ReviewDecision.REUSE_EXISTING: GateDecision.REUSE_RECOMMENDED,
        ReviewDecision.MANUAL_REVIEW: GateDecision.MANUAL_REVIEW,
        ReviewDecision.UPHOLD_REJECTION: GateDecision.FAIL,
    }
    return mapping[decision]


def structured_to_details(structured: StructuredReviewResult) -> dict[str, Any]:
    return structured.model_dump(mode="json")


def make_semantic_review_gate(
    reviewer: SemanticReviewer,
    *,
    context_provider: Any | None = None,
) -> Any:
    """Return an extra-gate callable that never mutates the database.

    ``context_provider`` is an optional zero-arg callable returning a dict with
    actor_id / operation_log_id / request_id / trace_id / review_context /
    skip_llm for LLM logging and short-circuit.
    """

    def _gate(
        proposal_type: ProposalType,
        payload: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> GateOutcome | None:
        context = context_provider() if context_provider is not None else {}
        runtime = runtime or {}
        if runtime.get("deterministic_failed") or context.get("skip_llm"):
            lexical = context.get("lexical_candidates") or []
            # Prefer lexical candidates already attached by the similarity gate.
            for prior in runtime.get("prior_outcomes") or []:
                if getattr(prior, "gate_name", None) == "similarity":
                    lexical = (prior.details or {}).get("lexical_candidates") or lexical
            return GateOutcome(
                "semantic_review",
                GateDecision.PASS,
                {
                    "skipped": True,
                    "reason": "deterministic_failure",
                    "lexical_candidates": lexical,
                },
            )
        review_context = context.get("review_context")
        settings = context.get("settings") or get_settings()
        try:
            result = reviewer.review(
                ReviewRequest(
                    proposal_type=proposal_type,
                    payload=payload,
                    actor_id=context.get("actor_id"),
                    operation_log_id=context.get("operation_log_id"),
                    request_id=context.get("request_id"),
                    trace_id=context.get("trace_id"),
                    context=review_context,
                    review_stage="initial",
                )
            )
        except Exception as exc:  # noqa: BLE001 - degrade to manual_review
            SEMANTIC_REVIEW_METRICS.record_review(decision="manual_review")
            shadow = settings.semantic_review_mode == "shadow"
            return GateOutcome(
                "semantic_review",
                GateDecision.MANUAL_REVIEW,
                {
                    "decision": SemanticDecision.MANUAL_REVIEW.value,
                    "model_decision": SemanticDecision.MANUAL_REVIEW.value,
                    "confidence": 0.0,
                    "summary": "Semantic reviewer unavailable",
                    "reasons": [
                        {
                            "code": "semantic_reviewer_unavailable",
                            "message": "Semantic reviewer call failed",
                        }
                    ],
                    "related_existing_concepts": [],
                    "recommended_actions": ["Retry later", "Route to manual review"],
                    "context_sufficient": False,
                    "challengeable": True,
                    "reason": "reviewer_failure",
                    "error_type": type(exc).__name__,
                    "authoritative": not shadow,
                    "shadow": shadow,
                    "shadow_effect": "manual_review" if shadow else None,
                    "mode": "shadow" if shadow else settings.semantic_review_mode,
                },
            )

        structured = result.structured
        if structured is None:
            structured = StructuredReviewResult(
                decision=SemanticDecision(result.decision.value),
                confidence=0.0,
                summary=result.reason,
                reasons=[ReviewReason(code="unstructured", message=result.reason)],
                context_sufficient=True,
                challengeable=True,
            )
        proposal_key = str(
            payload.get("key") or payload.get("alias") or payload.get("child_key") or ""
        )
        structured = apply_confidence_policy(
            structured,
            settings=settings,
            proposal_key=proposal_key,
            proposal_description=str(payload.get("description") or ""),
        )
        model_decision = ReviewDecision(structured.decision.value)
        shadow = settings.semantic_review_mode == "shadow"
        # Shadow: persist/log the model judgment, but never bind proposal outcomes to it.
        gate_decision = (
            GateDecision.MANUAL_REVIEW if shadow else review_decision_to_gate(model_decision)
        )
        SEMANTIC_REVIEW_METRICS.record_review(
            decision=model_decision.value,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        details = {
            **structured_to_details(structured),
            "review_decision": model_decision.value,
            "model_decision": model_decision.value,
            "reason": structured.summary,
            **{k: v for k, v in result.details.items() if k not in {"structured"}},
            "prompt_template_version": result.prompt_template_version,
            "authoritative": not shadow,
            "shadow": shadow,
        }
        if shadow:
            details["shadow_effect"] = "manual_review"
            details["mode"] = "shadow"
        if review_context is not None:
            details["context_concept_keys"] = review_context.concept_keys()
            details["input_hash"] = review_context.input_hash
            details["context_builder_version"] = review_context.builder_version
            details["estimated_context_tokens"] = review_context.estimated_tokens
            details["candidate_selection_trace"] = list(
                review_context.candidate_selection_trace
            )
            details["derivation_hints"] = list(review_context.derivation_hints)
        return GateOutcome(
            "semantic_review",
            gate_decision,
            details,
        )

    _gate.gate_name = "semantic_review"  # type: ignore[attr-defined]
    _gate.skip_on_deterministic_fail = True  # type: ignore[attr-defined]
    _gate.requires_external_cost = True  # type: ignore[attr-defined]
    return _gate


def _status_for_decision(_decision: ReviewDecision, *, provider: str) -> LlmCallStatus:
    if provider == "disabled":
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
    payload_chars = len(str(request.payload))
    reason_chars = len(result.reason)
    return max(1, payload_chars // 4), max(1, reason_chars // 4)
