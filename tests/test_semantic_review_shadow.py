"""Shadow-mode and eval-corpus orchestration tests (non-authoritative)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from semantic_memory.config import Settings
from semantic_memory.models import ActorType, ProposalStatus
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import Cardinality, GateDecision, ValueKind
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import (
    ProposalOutcome,
    ProposeClassRequest,
    ProposePredicateRequest,
)
from semantic_memory.schemas.semantic_review import (
    AnswerSemanticClarificationRequest,
    ChallengeOntologyReviewRequest,
    SemanticDecision,
)
from semantic_memory.seeding.calibration_ontology import ensure_calibration_ontology
from semantic_memory.services.actors import ActorService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import MockSemanticReviewer, ReviewDecision

_CORPUS = Path(__file__).parent / "fixtures" / "semantic_review_eval_corpus.json"

_EQUIV = {
    "reject": {"reject", "reuse_existing", "uphold_rejection"},
    "reuse_existing": {"reject", "reuse_existing", "uphold_rejection"},
    "uphold_rejection": {"reject", "reuse_existing", "uphold_rejection"},
    "approve": {"approve"},
    "manual_review": {"manual_review"},
}


def _agrees(model: str | None, human: str | None) -> bool:
    if model is None or human is None:
        return False
    return model in _EQUIV.get(human, {human})


def _related_keys(case: dict) -> list[str]:
    keys: list[str] = []
    if case.get("expected_related_key"):
        keys.append(str(case["expected_related_key"]))
    for key in case.get("expected_related_keys") or []:
        keys.append(str(key))
    # Deduplicate, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _related_metadata(case: dict, *, kind: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key in _related_keys(case):
        item_kind = "class" if key[:1].isupper() else kind
        items.append({"kind": item_kind, "key": key, "reason": "Closest semantic match"})
    return items


def _ensure_proposer(session: Session, key: str = "shadow-proposer") -> str:
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


def test_shadow_mode_persists_model_decision_but_manual_outcome(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="shadow"),
        reviewer=MockSemanticReviewer(),
    )
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ShadowApproved",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.APPROVE.value,
                "review_confidence": 0.96,
            },
        )
    )
    assert result.outcome == ProposalOutcome.MANUAL_REVIEW
    assert result.proposal.status == ProposalStatus.IN_REVIEW
    semantic = next(
        item for item in result.proposal.gate_results if item.gate_name == "semantic_review"
    )
    assert semantic.decision == GateDecision.MANUAL_REVIEW
    assert semantic.details["model_decision"] == "approve"
    assert semantic.details["shadow"] is True
    assert semantic.details["authoritative"] is False
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert review.decision == SemanticDecision.APPROVE
    assert review.authoritative is False


def test_shadow_reject_does_not_reject_proposal(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="shadow"),
        reviewer=MockSemanticReviewer(),
    )
    result = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ShadowRejected",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REJECT.value,
                "review_confidence": 0.94,
                "related_existing_concepts": [
                    {"kind": "class", "key": "Role", "reason": "Near duplicate"}
                ],
            },
        )
    )
    assert result.outcome == ProposalOutcome.MANUAL_REVIEW
    assert result.proposal.effective_semantic_review is not None
    assert result.proposal.effective_semantic_review.decision == SemanticDecision.REJECT
    assert result.proposal.effective_semantic_review.authoritative is False


def test_shadow_challenge_overturn_stays_manual(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="shadow"),
        reviewer=MockSemanticReviewer(),
    )
    rejected = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ShadowResponsibility",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REJECT.value,
                "challenge_decision": ReviewDecision.APPROVE.value,
            },
        )
    )
    challenged = service.challenge_ontology_review(
        ChallengeOntologyReviewRequest(
            **_envelope(proposer),
            proposal_id=rejected.proposal.id,
            challenge_reason=(
                "Role represents positions held by agents, while Responsibility "
                "represents duties that may exist independently of a holder."
            ),
        )
    )
    assert challenged.outcome == ProposalOutcome.MANUAL_REVIEW.value
    assert challenged.review.decision == SemanticDecision.APPROVE
    assert challenged.review.authoritative is False
    assert challenged.review.decision_changed is True


def test_eval_corpus_mock_orchestration(db_session: Session) -> None:
    """Drive the calibration corpus under mock; asserts orchestration, not Terra quality."""
    ensure_calibration_ontology(db_session)
    corpus = json.loads(_CORPUS.read_text(encoding="utf-8"))
    proposer = _ensure_proposer(db_session, "corpus-proposer")
    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="shadow"),
        reviewer=MockSemanticReviewer(),
    )

    for case in corpus["cases"]:
        failure = case["failure_mode"]
        payload = case["payload"]
        if failure == "deterministic_short_circuit":
            result = service.propose_class(
                ProposeClassRequest(
                    **_envelope(proposer),
                    key=payload["key"],
                    description=payload.get("description"),
                    parent_keys=list(payload.get("parent_keys") or []),
                    metadata={"review_decision": ReviewDecision.APPROVE.value},
                )
            )
            assert result.outcome == ProposalOutcome.REJECTED, case["id"]
            semantic = next(
                item for item in result.proposal.gate_results if item.gate_name == "semantic_review"
            )
            assert semantic.details.get("skipped") is True, case["id"]
            continue

        expected = case.get("human_expected") or case.get("initial_human_expected")
        assert expected is not None, case["id"]
        kind = "predicate" if case["proposal_type"] == "predicate" else "class"
        metadata: dict[str, object] = {
            "review_decision": expected,
            "review_confidence": 0.9,
            "related_existing_concepts": _related_metadata(case, kind=kind),
        }
        if case.get("force_context_insufficient"):
            metadata["context_sufficient"] = False
            metadata["review_decision"] = ReviewDecision.APPROVE.value
            expected = "manual_review"
        followup = case.get("clarification_human_expected") or case.get("challenge_human_expected")
        if followup:
            metadata["challenge_decision"] = followup

        if case["proposal_type"] == "predicate":
            result = service.propose_predicate(
                ProposePredicateRequest(
                    **_envelope(proposer),
                    key=payload["key"],
                    label=payload.get("label"),
                    description=payload.get("description"),
                    value_kind=ValueKind(payload["value_kind"]),
                    cardinality=Cardinality(payload.get("cardinality") or "many"),
                    domain_keys=list(payload.get("domain_keys") or []),
                    range_keys=list(payload.get("range_keys") or []),
                    metadata=metadata,
                )
            )
        else:
            result = service.propose_class(
                ProposeClassRequest(
                    **_envelope(proposer),
                    key=payload["key"],
                    label=payload.get("label"),
                    description=payload.get("description"),
                    parent_keys=list(payload.get("parent_keys") or []),
                    metadata=metadata,
                )
            )

        assert result.outcome == ProposalOutcome.MANUAL_REVIEW, case["id"]
        review = result.proposal.effective_semantic_review
        assert review is not None, case["id"]
        assert _agrees(review.decision.value, expected), (
            case["id"],
            review.decision.value,
            expected,
        )

        if case.get("expects_clarification"):
            assert result.open_clarification_request is not None, case["id"]

        clar_text = case.get("clarification_response")
        open_clar = result.open_clarification_request
        if open_clar is not None and (clar_text or case.get("challenge_reason")):
            answered = service.answer_semantic_clarification(
                AnswerSemanticClarificationRequest(
                    **_envelope(proposer),
                    clarification_request_id=open_clar.clarification_request_id,
                    response=str(clar_text or case["challenge_reason"]),
                )
            )
            assert answered.outcome == ProposalOutcome.MANUAL_REVIEW.value, case["id"]
            expected_after = case.get("clarification_human_expected") or case.get(
                "challenge_human_expected"
            )
            assert _agrees(answered.review.decision.value, expected_after), case["id"]
        elif case.get("challenge_reason"):
            challenged = service.challenge_ontology_review(
                ChallengeOntologyReviewRequest(
                    **_envelope(proposer),
                    proposal_id=result.proposal.id,
                    challenge_reason=case["challenge_reason"],
                )
            )
            assert challenged.outcome == ProposalOutcome.MANUAL_REVIEW.value, case["id"]
            assert _agrees(
                challenged.review.decision.value,
                case.get("challenge_human_expected"),
            ), case["id"]
