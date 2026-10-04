"""Deterministic clarification-request lifecycle tests (AtlasSynapse-issued IDs)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    ClarificationRequestAlreadyResolvedError,
    ClarificationRequestNotFoundError,
    ClarificationRequestSupersededError,
)
from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import SemanticClarificationStatus
from semantic_memory.models.governance import OntologySemanticClarificationRequest
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import ProposeClassRequest
from semantic_memory.schemas.semantic_review import (
    CLARIFICATION_REASON_CODE,
    AnswerSemanticClarificationRequest,
    SemanticDecision,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import MockSemanticReviewer, ReviewDecision


def _ensure_proposer(session: Session, key: str = "clar-proposer") -> str:
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


def _ambiguous_metadata(**extra: object) -> dict[str, object]:
    meta: dict[str, object] = {
        "review_decision": ReviewDecision.MANUAL_REVIEW.value,
        "review_confidence": 0.94,
        "related_existing_concepts": [
            {"kind": "class", "key": "Skill", "reason": "Potential semantic overlap"}
        ],
        "review_reasons": [
            {
                "code": CLARIFICATION_REASON_CODE,
                "message": "Distinction from Skill is unclear.",
            }
        ],
    }
    meta.update(extra)
    return meta


def test_ambiguous_review_issues_clarification_request_ids(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="CapabilityArea",
            description="Some kind of ability or focus area related to agents.",
            parent_keys=["Thing"],
            metadata=_ambiguous_metadata(),
        )
    )
    assert result.open_clarification_request is not None
    clar = result.open_clarification_request
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert clar.proposal_id == result.proposal.id
    assert clar.review_id == review.id
    assert clar.clarification_request_id is not None
    assert clar.reason_code == CLARIFICATION_REASON_CODE
    assert clar.clarification_status == SemanticClarificationStatus.OPEN
    assert clar.supersedes_clarification_request_id is None
    assert clar.resolved_by_review_id is None
    assert clar.required_clarification
    assert review.clarification_request_id == clar.clarification_request_id


def test_second_clarification_supersedes_first_deterministically(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session, key="clar-supersede")
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    first = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="CapabilityArea",
            description="Some kind of ability or focus area related to agents.",
            parent_keys=["Thing"],
            metadata=_ambiguous_metadata(),
        )
    )
    first_clar = first.open_clarification_request
    assert first_clar is not None
    first_id = first_clar.clarification_request_id
    proposal = first.proposal

    # Force a second open ask against a new review on the same proposal.
    from semantic_memory.models.enums import SemanticReviewStage

    review2 = service._governance.create_semantic_review(
        proposal_id=proposal.id,
        review_stage=SemanticReviewStage.CHALLENGE,
        previous_review_id=first_clar.review_id,
        provider="mock",
        model="mock-reviewer",
        prompt_template_version="test",
        context_builder_version="test",
        input_hash="hash-2",
        decision=SemanticDecision.MANUAL_REVIEW.value,
        summary="Still ambiguous after more context.",
        reasons=[
            {
                "code": CLARIFICATION_REASON_CODE,
                "message": "Still unclear vs Skill.",
            }
        ],
        related_existing_concepts=[
            {"kind": "class", "key": "Skill", "reason": "Still overlapping"}
        ],
        recommended_actions=[],
        context_sufficient=True,
        challengeable=True,
        authoritative=True,
        details={
            "required_clarification": [
                "Explain how CapabilityArea differs from Skill after second pass."
            ]
        },
    )
    service._governance.set_effective_semantic_review(
        service._governance.get_proposal(proposal.id), review2.id
    )
    second = service._maybe_issue_clarification_request(
        proposal=service._governance.get_proposal(proposal.id),
        review=review2,
    )
    assert second is not None
    assert second.id != first_id
    assert second.supersedes_clarification_request_id == first_id
    assert second.status == SemanticClarificationStatus.OPEN.value

    prior = db_session.get(OntologySemanticClarificationRequest, first_id)
    assert prior is not None
    assert prior.status == SemanticClarificationStatus.SUPERSEDED.value


def test_answer_to_superseded_clarification_hard_rejects(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session, key="clar-stale")
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    first = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="CapabilityArea",
            description="Some kind of ability or focus area related to agents.",
            parent_keys=["Thing"],
            metadata=_ambiguous_metadata(),
        )
    )
    first_clar = first.open_clarification_request
    assert first_clar is not None
    stale_id = first_clar.clarification_request_id

    from semantic_memory.models.enums import SemanticReviewStage

    review2 = service._governance.create_semantic_review(
        proposal_id=first.proposal.id,
        review_stage=SemanticReviewStage.CHALLENGE,
        previous_review_id=first_clar.review_id,
        provider="mock",
        model="mock-reviewer",
        prompt_template_version="test",
        context_builder_version="test",
        input_hash="hash-stale",
        decision=SemanticDecision.MANUAL_REVIEW.value,
        summary="Still ambiguous.",
        reasons=[{"code": CLARIFICATION_REASON_CODE, "message": "unclear"}],
        related_existing_concepts=[
            {"kind": "class", "key": "Skill", "reason": "overlap"}
        ],
        details={"required_clarification": ["Clarify vs Skill again."]},
    )
    proposal = service._governance.get_proposal(first.proposal.id)
    assert proposal is not None
    service._governance.set_effective_semantic_review(proposal, review2.id)
    second = service._maybe_issue_clarification_request(proposal=proposal, review=review2)
    assert second is not None
    reviews_before = len(service._governance.list_semantic_reviews(proposal.id))

    with pytest.raises(ClarificationRequestSupersededError):
        service.answer_semantic_clarification(
            AnswerSemanticClarificationRequest(
                **_envelope(proposer),
                clarification_request_id=stale_id,
                response=(
                    "This answer targets a superseded clarification and must be rejected "
                    "without creating another review."
                ),
            )
        )
    reviews_after = len(service._governance.list_semantic_reviews(proposal.id))
    assert reviews_after == reviews_before


def test_clarification_answer_that_stays_manual_opens_new_request(
    db_session: Session,
) -> None:
    proposer = _ensure_proposer(db_session, key="clar-loop")
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    proposed = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="CapabilityArea",
            description="Some kind of ability or focus area related to agents.",
            parent_keys=["Thing"],
            metadata=_ambiguous_metadata(
                # Re-review after answer stays ambiguous.
                challenge_decision=ReviewDecision.MANUAL_REVIEW.value,
            ),
        )
    )
    first = proposed.open_clarification_request
    assert first is not None

    answered = service.answer_semantic_clarification(
        AnswerSemanticClarificationRequest(
            **_envelope(proposer),
            clarification_request_id=first.clarification_request_id,
            response=(
                "Maybe related to Skill but still not sure whether this is a domain "
                "grouping or a competence; need more ontology guidance."
            ),
        )
    )
    assert answered.clarification_request_id == first.clarification_request_id
    assert answered.clarification_status == SemanticClarificationStatus.RESOLVED
    assert answered.resolved_by_review_id == answered.review.id
    assert answered.review.decision == SemanticDecision.MANUAL_REVIEW

    nxt = answered.open_clarification_request
    assert nxt is not None
    assert nxt.clarification_request_id != first.clarification_request_id
    assert nxt.review_id == answered.review.id
    assert nxt.clarification_status == SemanticClarificationStatus.OPEN
    assert nxt.supersedes_clarification_request_id is None  # prior was resolved, not open
    assert nxt.resolved_by_review_id is None

    prior = db_session.get(
        OntologySemanticClarificationRequest, first.clarification_request_id
    )
    assert prior is not None
    assert prior.status == SemanticClarificationStatus.RESOLVED.value
    assert prior.resulting_review_id == answered.review.id


def test_answer_clarification_reuses_request_id_and_rejects_replay(
    db_session: Session,
) -> None:
    proposer = _ensure_proposer(db_session, key="clar-answerer")
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    proposed = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="CapabilityArea",
            description="Some kind of ability or focus area related to agents.",
            parent_keys=["Thing"],
            metadata=_ambiguous_metadata(
                challenge_decision=ReviewDecision.APPROVE.value,
            ),
        )
    )
    clar = proposed.open_clarification_request
    assert clar is not None

    answered = service.answer_semantic_clarification(
        AnswerSemanticClarificationRequest(
            **_envelope(proposer),
            clarification_request_id=clar.clarification_request_id,
            response=(
                "CapabilityArea is a domain grouping such as Data Engineering. "
                "Skill is a concrete competence such as SQL optimization. "
                "A person may have multiple Skills under one CapabilityArea."
            ),
        )
    )
    assert answered.clarification_request_id == clar.clarification_request_id
    assert answered.clarification_status == SemanticClarificationStatus.RESOLVED
    assert answered.resolved_by_review_id == answered.review.id
    assert answered.review.decision == SemanticDecision.APPROVE
    assert answered.open_clarification_request is None

    with pytest.raises(ClarificationRequestAlreadyResolvedError):
        service.answer_semantic_clarification(
            AnswerSemanticClarificationRequest(
                **_envelope(proposer),
                clarification_request_id=clar.clarification_request_id,
                response="Trying to answer the same clarification again with more text.",
            )
        )


def test_unknown_clarification_request_id_fails(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session, key="clar-missing")
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    with pytest.raises(ClarificationRequestNotFoundError):
        service.answer_semantic_clarification(
            AnswerSemanticClarificationRequest(
                **_envelope(proposer),
                clarification_request_id=uuid.uuid4(),
                response="This should fail because the clarification id does not exist.",
            )
        )


def test_vague_confident_reuse_redirects_to_clarification(db_session: Session) -> None:
    """AtlasSynapse must not accept confident reuse when wording is multi-model-vague."""
    proposer = _ensure_proposer(db_session, key="clar-vague-reuse")
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="CapabilityArea",
            description="Some kind of ability or focus area related to agents.",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REUSE_EXISTING.value,
                "review_confidence": 0.93,
                "related_existing_concepts": [
                    {
                        "kind": "class",
                        "key": "Skill",
                        "reason": "Overlaps agent ability language",
                    }
                ],
                "review_reasons": [
                    {
                        "code": "near_duplicate",
                        "message": "Looks like Skill.",
                    }
                ],
            },
        )
    )
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert review.decision == SemanticDecision.MANUAL_REVIEW
    assert result.open_clarification_request is not None
    assert any(
        r.code == CLARIFICATION_REASON_CODE for r in review.reasons
    )
    assert result.open_clarification_request.required_clarification
