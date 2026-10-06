"""Acceptance tests for LLM semantic review quality gate (mock-first)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.config import Settings
from semantic_memory.models import ActorType, OntologyClass, ProposalStatus
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import GateDecision, ProposalType, SemanticReviewStage
from semantic_memory.models.governance import OntologyGateResult, OntologySemanticReview
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import ProposalOutcome, ProposeClassRequest
from semantic_memory.schemas.semantic_review import ChallengeOntologyReviewRequest, SemanticDecision
from semantic_memory.services.actors import ActorService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import (
    FailingSemanticReviewer,
    MockSemanticReviewer,
    ReviewDecision,
)
from semantic_memory.services.review_context import SemanticReviewContextBuilder
from semantic_memory.services.review_metrics import SEMANTIC_REVIEW_METRICS


def _ensure_proposer(session: Session, key: str = "sem-gate-proposer") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def _envelope(actor_key: str) -> dict[str, object]:
    return {
        "actor_key": actor_key,
        "request_id": uuid.uuid4(),
        "idempotency_key": f"idem-{uuid.uuid4()}",
    }


def test_clear_approval(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="TelemetryChannel",
            description="A distinct communication channel for telemetry streams.",
            parent_keys=["Thing"],
            metadata={"review_decision": ReviewDecision.APPROVE.value, "review_confidence": 0.91},
        )
    )
    assert result.outcome == ProposalOutcome.READY_TO_APPLY
    assert result.proposal.effective_semantic_review is not None
    assert result.proposal.effective_semantic_review.decision == SemanticDecision.APPROVE


def test_duplicate_rejected_with_related_concept(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="JobTitle",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REJECT.value,
                "review_confidence": 0.93,
                "related_existing_concepts": [
                    {"kind": "class", "key": "Role", "reason": "Closest semantic match."}
                ],
                "recommended_actions": ["Reuse Role", "Clarify distinction"],
                "review_reasons": [
                    {
                        "code": "probable_duplicate",
                        "message": "Overlaps strongly with existing class Role.",
                    }
                ],
            },
        )
    )
    assert result.outcome == ProposalOutcome.REJECTED
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert review.decision == SemanticDecision.REJECT
    assert review.related_existing_concepts[0].key == "Role"
    assert review.challengeable is True
    semantic_gate = next(
        item for item in result.proposal.gate_results if item.gate_name == "semantic_review"
    )
    assert semantic_gate.decision == GateDecision.FAIL
    assert semantic_gate.details["related_existing_concepts"][0]["key"] == "Role"


def test_ambiguity_and_context_insufficient_manual_review(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    ambiguous = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="AmbiguousThing",
            parent_keys=["Thing"],
            metadata={"review_decision": ReviewDecision.APPROVE.value, "review_confidence": 0.4},
        )
    )
    assert ambiguous.outcome == ProposalOutcome.MANUAL_REVIEW

    insufficient = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ThinContext",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.APPROVE.value,
                "review_confidence": 0.99,
                "context_sufficient": False,
            },
        )
    )
    assert insufficient.outcome == ProposalOutcome.MANUAL_REVIEW


def test_challenge_overturn_and_lineage(db_session: Session) -> None:
    SEMANTIC_REVIEW_METRICS.reset()
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    rejected = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="Responsibility",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REJECT.value,
                "review_confidence": 0.9,
                "related_existing_concepts": [
                    {"kind": "class", "key": "Role", "reason": "Near duplicate"}
                ],
                "challenge_decision": ReviewDecision.APPROVE.value,
            },
        )
    )
    assert rejected.outcome == ProposalOutcome.REJECTED
    initial_gate = next(
        item for item in rejected.proposal.gate_results if item.gate_name == "semantic_review"
    )
    assert initial_gate.decision == GateDecision.FAIL

    challenged = service.challenge_ontology_review(
        ChallengeOntologyReviewRequest(
            **_envelope(proposer),
            proposal_id=rejected.proposal.id,
            challenge_reason=(
                "Role represents positions held by agents, while Responsibility "
                "represents duties that may exist independently of a holder."
            ),
            proposed_revision={
                "description": "A duty attached to a role, independent of a holder."
            },
        )
    )
    assert challenged.outcome == ProposalOutcome.READY_TO_APPLY.value
    assert challenged.review.decision == SemanticDecision.APPROVE
    assert challenged.review.previous_decision == SemanticDecision.REJECT
    assert challenged.review.decision_changed is True
    assert challenged.review.review_stage == SemanticReviewStage.CHALLENGE

    # Historical gate row must remain the original fail (append-only).
    gate_after = db_session.scalar(
        select(OntologyGateResult).where(
            OntologyGateResult.proposal_id == rejected.proposal.id,
            OntologyGateResult.gate_name == "semantic_review",
        )
    )
    assert gate_after is not None
    assert gate_after.decision == GateDecision.FAIL.value

    refreshed = service.get_proposal(rejected.proposal.id)
    assert refreshed.status == ProposalStatus.SUBMITTED
    assert len(refreshed.semantic_reviews) == 2
    assert refreshed.effective_semantic_review is not None
    assert refreshed.effective_semantic_review.decision == SemanticDecision.APPROVE
    assert SEMANTIC_REVIEW_METRICS.snapshot()["semantic_review_overturns_total"] == 1


def test_challenge_upheld(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    rejected = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="RoleClone",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REJECT.value,
                "review_confidence": 0.92,
                "challenge_decision": ReviewDecision.UPHOLD_REJECTION.value,
            },
        )
    )
    challenged = service.challenge_ontology_review(
        ChallengeOntologyReviewRequest(
            **_envelope(proposer),
            proposal_id=rejected.proposal.id,
            challenge_reason=(
                "Still think this is different somehow because naming preferences differ."
            ),
        )
    )
    assert challenged.outcome == ProposalOutcome.REJECTED.value
    assert challenged.review.decision == SemanticDecision.UPHOLD_REJECTION
    assert challenged.review.decision_changed is False


def test_deterministic_failure_skips_llm(db_session: Session) -> None:
    calls: list[str] = []

    class CountingReviewer:
        provider = "mock"
        model = "counting"

        def review(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.proposal_type.value)
            return MockSemanticReviewer().review(request)

    proposer = _ensure_proposer(db_session)
    before = db_session.scalar(select(func.count()).select_from(OntologySemanticReview))
    service = ProposalService(db_session, reviewer=CountingReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="Broken",
            parent_keys=["MissingParent"],
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    assert result.outcome == ProposalOutcome.REJECTED
    assert calls == []
    after = db_session.scalar(select(func.count()).select_from(OntologySemanticReview))
    assert after == before
    semantic = next(
        item for item in result.proposal.gate_results if item.gate_name == "semantic_review"
    )
    assert semantic.details.get("skipped") is True
    assert semantic.details.get("reason") == "deterministic_failure"
    # Lexical feedback may still be present from the similarity/local path.
    assert "lexical_candidates" in semantic.details or any(
        item.gate_name == "similarity" for item in result.proposal.gate_results
    )


def test_context_budget_excludes_unrelated_entities(db_session: Session) -> None:
    from semantic_memory.seeding.calibration_ontology import ensure_calibration_ontology

    ensure_calibration_ontology(db_session)
    db_session.flush()
    context = SemanticReviewContextBuilder(
        db_session,
        Settings(semantic_review_max_candidates=8, semantic_review_max_context_tokens=2000),
    ).build(
        proposal_type=ProposalType.CLASS,
        payload={
            "namespace_key": "core",
            "key": "Responsibility",
            "parent_keys": ["Thing"],
            "description": "Duties attached to a role",
        },
    )
    keys = " ".join(context.concept_keys()).casefold()
    assert "didac" not in keys
    assert "airbus" not in keys
    assert context.estimated_tokens <= 2000
    # Relevance-ranked context should include Role; Thing may be truncated as generic.
    assert any(item.key == "Role" for item in context.concepts)


def test_provider_failure_manual_review(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=FailingSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ProviderDown",
            parent_keys=["Organization"],
        )
    )
    assert result.outcome == ProposalOutcome.MANUAL_REVIEW
    semantic = next(
        item for item in result.proposal.gate_results if item.gate_name == "semantic_review"
    )
    assert semantic.decision == GateDecision.MANUAL_REVIEW
    assert semantic.details["reasons"][0]["code"] == "semantic_reviewer_unavailable"
    # Must not create ontology rows.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OntologyClass)
            .where(OntologyClass.key == "ProviderDown")
        )
        == 0
    )


def test_insubstantive_challenge_rejected(db_session: Session) -> None:
    from semantic_memory.exceptions import ValidationFailedError

    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    rejected = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="TempRole",
            parent_keys=["Thing"],
            metadata={"review_decision": ReviewDecision.REJECT.value},
        )
    )
    try:
        service.challenge_ontology_review(
            ChallengeOntologyReviewRequest(
                **_envelope(proposer),
                proposal_id=rejected.proposal.id,
                challenge_reason="Please reconsider.",
            )
        )
        raise AssertionError("expected ValidationFailedError")
    except ValidationFailedError as exc:
        assert exc.details.get("reason") == "challenge_insubstantive"
