"""Bounded semantic adjudication for entity identity (PR5)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from semantic_memory.config import Settings, get_settings
from semantic_memory.schemas.identity import (
    CandidateDecision,
    IdentityCandidate,
    IdentityResolutionOutcome,
    aggregate_candidate_decisions,
)
from semantic_memory.schemas.identity_adjudication import (
    IdentityAdjudicationRequest,
    IdentityAdjudicationResult,
    IdentityCandidateAdjudication,
)
from semantic_memory.services.openai_identity_adjudicator import (
    IDENTITY_ADJUDICATION_PROMPT_VERSION,
    OpenAIIdentityAdjudicator,
)

# Re-export for tests / callers that import the prompt version from this module.
__all__ = [
    "IDENTITY_ADJUDICATION_PROMPT_VERSION",
    "DisabledIdentityAdjudicator",
    "ExternalIdentityAdjudicator",
    "IdentityAdjudicationService",
    "IdentityAdjudicator",
    "MockIdentityAdjudicator",
    "apply_adjudication_to_candidates",
    "build_identity_adjudicator",
    "deterministic_blocks_llm_adjudication",
    "llm_adjudication_may_run",
]


@runtime_checkable
class IdentityAdjudicator(Protocol):
    def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult: ...


def deterministic_blocks_llm_adjudication(candidates: list[IdentityCandidate]) -> bool:
    """True when decisive deterministic evidence must not be overridden."""
    if any(item.decision == CandidateDecision.SAME for item in candidates):
        return True
    return not any(item.decision == CandidateDecision.UNCERTAIN for item in candidates)


def llm_adjudication_may_run(
    *,
    resolution: IdentityResolutionOutcome,
    candidates: list[IdentityCandidate],
) -> bool:
    if resolution != IdentityResolutionOutcome.AMBIGUOUS:
        return False
    return not deterministic_blocks_llm_adjudication(candidates)


def apply_adjudication_to_candidates(
    candidates: list[IdentityCandidate],
    adjudication: IdentityAdjudicationResult,
) -> list[IdentityCandidate]:
    """Apply LLM decisions only to UNCERTAIN candidates; never override decisive rows."""
    decisions = {item.entity_id: item.decision for item in adjudication.candidate_decisions}
    merged: list[IdentityCandidate] = []
    for candidate in candidates:
        if candidate.decision != CandidateDecision.UNCERTAIN:
            merged.append(candidate)
            continue
        proposed = decisions.get(candidate.entity_id, CandidateDecision.UNCERTAIN)
        if proposed not in {
            CandidateDecision.SAME,
            CandidateDecision.DIFFERENT,
            CandidateDecision.UNCERTAIN,
        }:
            proposed = CandidateDecision.UNCERTAIN
        merged.append(candidate.model_copy(update={"decision": proposed}))
    return merged


class DisabledIdentityAdjudicator:
    def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult:
        return IdentityAdjudicationResult(
            provider="disabled",
            model="none",
            prompt_template_version=IDENTITY_ADJUDICATION_PROMPT_VERSION,
            metadata={"skipped": True, "reason": "identity_review_disabled"},
        )


class MockIdentityAdjudicator:
    """Test/dev adjudicator: keeps UNCERTAIN unless a caller injects another adjudicator."""

    def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult:
        decisions: list[IdentityCandidateAdjudication] = []
        for candidate in request.candidates:
            if candidate.decision != CandidateDecision.UNCERTAIN:
                continue
            decisions.append(
                IdentityCandidateAdjudication(
                    entity_id=candidate.entity_id,
                    decision=CandidateDecision.UNCERTAIN,
                    summary="mock: no override",
                )
            )
        return IdentityAdjudicationResult(
            candidate_decisions=decisions,
            provider="mock",
            model="mock",
            prompt_template_version=IDENTITY_ADJUDICATION_PROMPT_VERSION,
            metadata={"mode": "mock"},
        )


class ExternalIdentityAdjudicator:
    """Provider-backed adjudication (fail closed to UNCERTAIN on errors)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._inner = OpenAIIdentityAdjudicator(settings)

    def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult:
        try:
            return self._inner.adjudicate(request)
        except Exception as exc:  # noqa: BLE001 — fail closed
            return IdentityAdjudicationResult(
                candidate_decisions=[
                    IdentityCandidateAdjudication(
                        entity_id=candidate.entity_id,
                        decision=CandidateDecision.UNCERTAIN,
                    )
                    for candidate in request.candidates
                    if candidate.decision == CandidateDecision.UNCERTAIN
                ],
                provider=self._settings.identity_review_provider,
                model=self._settings.identity_review_model,
                prompt_template_version=IDENTITY_ADJUDICATION_PROMPT_VERSION,
                metadata={
                    "fail_closed": True,
                    "error": str(exc)[:300],
                },
            )


def build_identity_adjudicator(settings: Settings | None = None) -> IdentityAdjudicator:
    cfg = settings or get_settings()
    mode = cfg.identity_review_mode
    if mode == "mock":
        return MockIdentityAdjudicator()
    if mode in {"shadow", "external"}:
        return ExternalIdentityAdjudicator(cfg)
    return DisabledIdentityAdjudicator()


class IdentityAdjudicationService:
    def __init__(
        self,
        adjudicator: IdentityAdjudicator | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._adjudicator = adjudicator or build_identity_adjudicator(self._settings)

    def maybe_adjudicate(
        self,
        *,
        request: IdentityAdjudicationRequest,
        resolution: IdentityResolutionOutcome,
        candidates: list[IdentityCandidate],
    ) -> tuple[list[IdentityCandidate], IdentityAdjudicationResult | None]:
        mode = self._settings.identity_review_mode
        if mode == "disabled":
            return candidates, None
        if not llm_adjudication_may_run(resolution=resolution, candidates=candidates):
            return candidates, None

        uncertain_only = [
            candidate
            for candidate in request.candidates
            if candidate.decision == CandidateDecision.UNCERTAIN
        ]
        payload = request.model_copy(update={"candidates": uncertain_only})
        result = self._adjudicator.adjudicate(payload)

        shadow = mode == "shadow"
        # Shadow audits model decisions but never enforces REUSE/CREATE changes.
        enforced = mode in {"mock", "external"} and not shadow
        result = result.model_copy(
            update={
                "shadow": shadow,
                "enforced": enforced,
                "metadata": {
                    **result.metadata,
                    "mode": mode,
                    "shadow": shadow,
                    "enforced": enforced,
                    "candidate_entity_ids": [str(c.entity_id) for c in uncertain_only],
                    "decisions": [
                        {
                            "entity_id": str(row.entity_id),
                            "candidate_id": row.candidate_id,
                            "decision": row.decision.value,
                            "llm_decision": (
                                None if row.llm_decision is None else row.llm_decision.value
                            ),
                            "cited_evidence_ids": row.cited_evidence_ids,
                            "rejected_invented_evidence_ids": row.rejected_invented_evidence_ids,
                        }
                        for row in result.candidate_decisions
                    ],
                    "provider": result.provider,
                    "model": result.model,
                    "prompt_template_version": result.prompt_template_version,
                },
            }
        )

        if not enforced:
            return candidates, result

        merged = apply_adjudication_to_candidates(candidates, result)
        return merged, result

    @staticmethod
    def resolution_after_adjudication(
        candidates: list[IdentityCandidate],
    ) -> IdentityResolutionOutcome:
        return aggregate_candidate_decisions(candidates)
