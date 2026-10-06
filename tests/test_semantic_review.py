"""Phase 11 semantic review adapter tests."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.config import Settings
from semantic_memory.models import ActorType, OntologyClass, ProposalStatus, ValueKind
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import Cardinality, GateDecision, ProposalType
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import (
    ApplyProposalRequest,
    ProposalOutcome,
    ProposeClassRequest,
    ProposePredicateRequest,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import (
    DisabledSemanticReviewer,
    FailingSemanticReviewer,
    MockSemanticReviewer,
    ReviewDecision,
    ReviewRequest,
    ReviewResult,
    make_semantic_review_gate,
    review_decision_to_gate,
)


def _ensure_proposer(session: Session, key: str = "review-proposer") -> str:
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


def test_disabled_reviewer_returns_manual_review() -> None:
    result = DisabledSemanticReviewer().review(
        ReviewRequest(proposal_type=ProposalType.CLASS, payload={"key": "X"})
    )
    assert result.decision == ReviewDecision.MANUAL_REVIEW


def test_mock_reviewer_decisions() -> None:
    reviewer = MockSemanticReviewer()
    for decision in ReviewDecision:
        result = reviewer.review(
            ReviewRequest(
                proposal_type=ProposalType.CLASS,
                payload={"metadata": {"review_decision": decision.value}},
            )
        )
        assert result.decision == decision
        assert review_decision_to_gate(decision) in {
            GateDecision.PASS,
            GateDecision.FAIL,
            GateDecision.REUSE_RECOMMENDED,
            GateDecision.MANUAL_REVIEW,
        }


def test_reviewer_failure_degrades_to_manual_review() -> None:
    gate = make_semantic_review_gate(FailingSemanticReviewer())
    outcome = gate(ProposalType.CLASS, {"namespace_key": "core", "key": "X"})
    assert outcome is not None
    assert outcome.decision == GateDecision.MANUAL_REVIEW
    assert outcome.details["reason"] == "reviewer_failure"


def test_disabled_reviewer_does_not_bypass_deterministic_gates(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="disabled"),
        reviewer=DisabledSemanticReviewer(),
    )
    before = db_session.scalar(select(func.count()).select_from(OntologyClass))
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="Broken",
            parent_keys=["MissingParent"],
        )
    )
    assert result.outcome == ProposalOutcome.REJECTED
    assert result.proposal.status == ProposalStatus.REJECTED
    after = db_session.scalar(select(func.count()).select_from(OntologyClass))
    assert after == before


def test_disabled_reviewer_marks_valid_proposal_manual_review(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="disabled"),
        reviewer=DisabledSemanticReviewer(),
    )
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ReviewLab",
            parent_keys=["Organization"],
        )
    )
    assert result.outcome == ProposalOutcome.MANUAL_REVIEW
    assert result.proposal.status == ProposalStatus.IN_REVIEW
    assert any(
        item.gate_name == "semantic_review" and item.decision == GateDecision.MANUAL_REVIEW
        for item in result.proposal.gate_results
    )
    # Authorized apply still works after manual review.
    applied = service.apply_proposal(
        ApplyProposalRequest(**_envelope("system"), proposal_id=result.proposal.id)
    )
    assert applied.outcome == ProposalOutcome.APPLIED


def test_mock_accept_reject_reuse(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())

    accepted = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="AcceptedLab",
            parent_keys=["Organization"],
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    assert accepted.outcome == ProposalOutcome.READY_TO_APPLY

    rejected = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="RejectedLab",
            parent_keys=["Organization"],
            metadata={"review_decision": ReviewDecision.REJECT.value},
        )
    )
    assert rejected.outcome == ProposalOutcome.REJECTED

    reuse = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ReuseLab",
            parent_keys=["Organization"],
            metadata={"review_decision": ReviewDecision.REUSE_EXISTING.value},
        )
    )
    assert reuse.outcome == ProposalOutcome.REUSE_RECOMMENDED


def test_reviewer_has_no_session_and_cannot_write(db_session: Session) -> None:
    reviewer = MockSemanticReviewer()
    assert not hasattr(reviewer, "_session")
    assert not hasattr(reviewer, "session")
    # Review call is pure; ontology row count unchanged.
    before = db_session.scalar(select(func.count()).select_from(OntologyClass))
    reviewer.review(
        ReviewRequest(
            proposal_type=ProposalType.PREDICATE,
            payload={
                "namespace_key": "core",
                "key": "shouldNotExist",
                "metadata": {"review_decision": ReviewDecision.APPROVE.value},
            },
        )
    )
    after = db_session.scalar(select(func.count()).select_from(OntologyClass))
    assert after == before


def test_knowledge_writes_do_not_invoke_reviewer(db_session: Session) -> None:
    """Routine knowledge writes must not call the semantic reviewer."""
    from semantic_memory.schemas.entities import CreateEntityRequest
    from semantic_memory.services.entities import EntityService

    calls: list[str] = []

    class CountingReviewer:
        def review(self, request: ReviewRequest) -> ReviewResult:
            calls.append(request.proposal_type.value)
            return DisabledSemanticReviewer().review(request)

    # Constructing ProposalService would register the reviewer; entity writes must not.
    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="kw-writer",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="kw-writer",
            request_id=uuid.uuid4(),
            idempotency_key=f"kw-{uuid.uuid4()}",
            canonical_name="No Review Entity",
            class_key="Person",
        )
    )
    assert calls == []
    # Explicitly prove a proposal would call it.
    ProposalService(db_session, reviewer=CountingReviewer()).propose_predicate(
        ProposePredicateRequest(
            actor_key="kw-writer",
            request_id=uuid.uuid4(),
            idempotency_key=f"prop-{uuid.uuid4()}",
            key="tempLink",
            value_kind=ValueKind.ENTITY,
            cardinality=Cardinality.MANY,
            domain_keys=["Person"],
            range_keys=["Person"],
        )
    )
    assert calls == ["predicate"]
