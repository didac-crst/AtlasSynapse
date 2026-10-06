#!/usr/bin/env python3
"""Run the non-production semantic-review eval corpus under shadow mode.

Scores end-to-end outcomes after clarification/challenge, not only first-pass
model responses. Requires SEMANTIC_REVIEW_MODE=shadow and SEMANTIC_REVIEW_API_KEY.

Usage (from apps/atlas-synapse, with DATABASE_URL pointing at a non-prod DB):

  SEMANTIC_REVIEW_MODE=shadow \\
  SEMANTIC_REVIEW_API_KEY=... \\
  python scripts/run_semantic_review_shadow_eval.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from semantic_memory.config import Settings, get_settings  # noqa: E402
from semantic_memory.models import ActorType  # noqa: E402
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES  # noqa: E402
from semantic_memory.models.enums import Cardinality, ValueKind  # noqa: E402
from semantic_memory.schemas.actors import ActorEnsureRequest  # noqa: E402
from semantic_memory.schemas.proposals import (  # noqa: E402
    ProposeClassRequest,
    ProposePredicateRequest,
)
from semantic_memory.schemas.semantic_review import (  # noqa: E402
    AnswerSemanticClarificationRequest,
    ChallengeOntologyReviewRequest,
)
from semantic_memory.seeding.calibration_ontology import (  # noqa: E402
    ensure_calibration_ontology,
)
from semantic_memory.services.actors import ActorService  # noqa: E402
from semantic_memory.services.proposals import ProposalService  # noqa: E402
from semantic_memory.services.review_metrics import SEMANTIC_REVIEW_METRICS  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "semantic_review_eval_corpus.json"

# Near-duplicate rejects and reuse recommendations are equivalent for calibration.
_EQUIV = {
    "reject": {"reject", "reuse_existing", "uphold_rejection"},
    "reuse_existing": {"reject", "reuse_existing", "uphold_rejection"},
    "uphold_rejection": {"reject", "reuse_existing", "uphold_rejection"},
    "approve": {"approve"},
    "manual_review": {"manual_review"},
}
_MANUAL = frozenset({"manual_review"})
_ASSERTIVE = frozenset({"approve", "reject", "reuse_existing", "uphold_rejection"})


def _agrees(model: str | None, human: str | None) -> bool:
    if model is None or human is None:
        return False
    return model in _EQUIV.get(human, {human})


def _wrong_kind(model: str | None, human: str | None) -> str:
    """unsafe = assertive wrong call; conservative = deferred when decisive expected."""
    if model in _MANUAL and human not in _MANUAL:
        return "conservative"
    return "unsafe"


def _score(
    *,
    model: str | None,
    human: str | None,
    confidence: float | None,
    label: str,
    counters: dict[str, int],
    initial: bool = False,
) -> None:
    if human is None or human in {"deterministic_reject"}:
        return
    counters["compared"] += 1
    if _agrees(model, human):
        return
    if (confidence or 0) < 0.85:
        print(f"  !! disagree_below_threshold ({label})")
        return
    kind = _wrong_kind(model, human)
    counters["wrong_confident"] += 1
    if kind == "unsafe":
        counters["unsafe_wrong"] += 1
        if initial and model in _ASSERTIVE:
            counters["unsafe_wrong_confident_initial"] += 1
    else:
        counters["conservative_wrong"] += 1
    print(f"  !! wrong_confident_decision ({label}, {kind})")


def main() -> int:
    get_settings.cache_clear()
    settings = Settings()
    if settings.semantic_review_mode != "shadow":
        print("Refusing to run: set SEMANTIC_REVIEW_MODE=shadow", file=sys.stderr)
        return 2
    if not settings.semantic_review_api_key:
        print("Refusing to run: SEMANTIC_REVIEW_API_KEY is empty", file=sys.stderr)
        return 2

    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    counters = {
        "compared": 0,
        "wrong_confident": 0,
        "unsafe_wrong": 0,
        "conservative_wrong": 0,
        "unsafe_wrong_confident_initial": 0,
        "ambiguous_with_clarification": 0,
        "ambiguous_missing_clarification": 0,
        "clarification_converged": 0,
        "clarification_failed": 0,
        "challenge_converged": 0,
        "challenge_failed": 0,
        "deterministic_ok": 0,
        "deterministic_bad": 0,
        "canonical_model_violation_approvals": 0,
    }
    SEMANTIC_REVIEW_METRICS.reset()

    with SessionLocal() as session:
        ActorService(session).ensure(
            ActorEnsureRequest(
                key="shadow-eval",
                actor_type=ActorType.AGENT,
                capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            )
        )
        seeded = ensure_calibration_ontology(session)
        session.commit()
        print(f"calibration_ontology={seeded}")
        service = ProposalService(session, settings=settings)

        for case in corpus["cases"]:
            print(f"\n=== {case['id']} ({case['failure_mode']}) ===")
            payload = case["payload"]
            env = {
                "actor_key": "shadow-eval",
                "request_id": uuid.uuid4(),
                "idempotency_key": f"shadow-eval-{uuid.uuid4()}",
            }
            if case["failure_mode"] == "deterministic_short_circuit":
                result = service.propose_class(
                    ProposeClassRequest(
                        **env,
                        key=payload["key"],
                        description=payload.get("description"),
                        parent_keys=list(payload.get("parent_keys") or []),
                    )
                )
                session.commit()
                semantic = next(
                    g for g in result.proposal.gate_results if g.gate_name == "semantic_review"
                )
                skipped = bool(semantic.details.get("skipped"))
                print(
                    f"outcome={result.outcome} skipped={skipped} "
                    f"reason={semantic.details.get('reason')}"
                )
                if skipped:
                    counters["deterministic_ok"] += 1
                else:
                    counters["deterministic_bad"] += 1
                    print("  !! deterministic failure called LLM")
                continue

            if case["proposal_type"] == "predicate":
                result = service.propose_predicate(
                    ProposePredicateRequest(
                        **env,
                        key=payload["key"],
                        label=payload.get("label"),
                        description=payload.get("description"),
                        value_kind=ValueKind(payload["value_kind"]),
                        cardinality=Cardinality(payload.get("cardinality") or "many"),
                        domain_keys=list(payload.get("domain_keys") or []),
                        range_keys=list(payload.get("range_keys") or []),
                    )
                )
            else:
                force_thin = bool(case.get("force_context_insufficient"))
                tiny = Settings(
                    **{
                        **settings.model_dump(),
                        "semantic_review_max_context_tokens": (
                            200 if force_thin else settings.semantic_review_max_context_tokens
                        ),
                        "semantic_review_max_candidates": (
                            1 if force_thin else settings.semantic_review_max_candidates
                        ),
                    }
                )
                local = ProposalService(session, settings=tiny) if force_thin else service
                result = local.propose_class(
                    ProposeClassRequest(
                        **env,
                        key=payload["key"],
                        label=payload.get("label"),
                        description=payload.get("description"),
                        parent_keys=list(payload.get("parent_keys") or []),
                    )
                )
            session.commit()

            review = result.proposal.effective_semantic_review
            model = None if review is None else review.decision.value
            conf = None if review is None else review.confidence
            initial_human = case.get("initial_human_expected") or case.get("human_expected")
            final_human = (
                case.get("clarification_human_expected")
                or case.get("challenge_human_expected")
                or case.get("human_expected")
                or initial_human
            )
            print(
                f"initial outcome={result.outcome} model_decision={model} "
                f"confidence={conf} human_expected={initial_human} "
                f"authoritative={None if review is None else review.authoritative}"
            )
            if review is not None:
                print(f"summary={review.summary!r}")
                if review.related_existing_concepts:
                    print(
                        "related="
                        + ", ".join(f"{c.kind}:{c.key}" for c in review.related_existing_concepts)
                    )
                if result.open_clarification_request is not None:
                    clar = result.open_clarification_request
                    print(
                        "clarification="
                        f"id={clar.clarification_request_id} "
                        f"status={clar.clarification_status.value} "
                        f"question={clar.question!r}"
                    )

            # Initial-pass unsafe assertive errors (promotion gate).
            if review is not None and initial_human:
                if (
                    not _agrees(model, initial_human)
                    and (conf or 0) >= 0.85
                    and model in _ASSERTIVE
                    and _wrong_kind(model, initial_human) == "unsafe"
                ):
                    counters["unsafe_wrong_confident_initial"] += 1
                    print("  !! unsafe_wrong_confident_initial")
                if (
                    case.get("canonical_model_case")
                    and model == "approve"
                    and (conf or 0) >= 0.85
                    and initial_human not in {None, "approve"}
                ):
                    counters["canonical_model_violation_approvals"] += 1
                    print(
                        "  !! canonical_model_violation_approvals "
                        f"bucket={case.get('policy_bucket')}"
                    )

            expects_clarification = bool(case.get("expects_clarification"))
            open_clar = result.open_clarification_request
            if expects_clarification:
                if open_clar is not None:
                    counters["ambiguous_with_clarification"] += 1
                else:
                    counters["ambiguous_missing_clarification"] += 1
                    print("  !! expected clarification_request missing")

            final_model = model
            final_conf = conf

            # Clarification path (preferred for ambiguity).
            clar_text = case.get("clarification_response")
            if open_clar is not None and clar_text:
                answered = service.answer_semantic_clarification(
                    AnswerSemanticClarificationRequest(
                        actor_key="shadow-eval",
                        request_id=uuid.uuid4(),
                        idempotency_key=f"shadow-clar-{uuid.uuid4()}",
                        clarification_request_id=open_clar.clarification_request_id,
                        response=clar_text,
                    )
                )
                session.commit()
                final_model = answered.review.decision.value
                final_conf = answered.review.confidence
                print(
                    f"clarification answer → model={final_model} "
                    f"confidence={final_conf} "
                    f"status={answered.clarification_status.value} "
                    f"open_next={answered.open_clarification_request is not None}"
                )
                print(f"summary={answered.review.summary!r}")
                ch_human = case.get("clarification_human_expected") or final_human
                if _agrees(final_model, ch_human):
                    counters["clarification_converged"] += 1
                else:
                    counters["clarification_failed"] += 1
                _score(
                    model=final_model,
                    human=ch_human,
                    confidence=final_conf,
                    label="clarification",
                    counters=counters,
                )
            elif case.get("challenge_reason") and review is not None:
                # Challenge path (or clarification missing but challenge available).
                # If a clarification was opened without a scripted answer, use
                # challenge_reason as the clarification response when present.
                if open_clar is not None and not clar_text:
                    answered = service.answer_semantic_clarification(
                        AnswerSemanticClarificationRequest(
                            actor_key="shadow-eval",
                            request_id=uuid.uuid4(),
                            idempotency_key=f"shadow-clar-{uuid.uuid4()}",
                            clarification_request_id=open_clar.clarification_request_id,
                            response=case["challenge_reason"],
                        )
                    )
                    session.commit()
                    final_model = answered.review.decision.value
                    final_conf = answered.review.confidence
                    print(
                        f"clarification(via challenge_reason) → model={final_model} "
                        f"confidence={final_conf}"
                    )
                    print(f"summary={answered.review.summary!r}")
                    ch_human = case.get("challenge_human_expected") or final_human
                    if _agrees(final_model, ch_human):
                        counters["clarification_converged"] += 1
                        counters["challenge_converged"] += 1
                    else:
                        counters["clarification_failed"] += 1
                        counters["challenge_failed"] += 1
                    _score(
                        model=final_model,
                        human=ch_human,
                        confidence=final_conf,
                        label="clarification",
                        counters=counters,
                    )
                else:
                    challenged = service.challenge_ontology_review(
                        ChallengeOntologyReviewRequest(
                            actor_key="shadow-eval",
                            request_id=uuid.uuid4(),
                            idempotency_key=f"shadow-challenge-{uuid.uuid4()}",
                            proposal_id=result.proposal.id,
                            challenge_reason=case["challenge_reason"],
                        )
                    )
                    session.commit()
                    final_model = challenged.review.decision.value
                    final_conf = challenged.review.confidence
                    ch_human = case.get("challenge_human_expected")
                    print(
                        f"challenge → model={final_model} "
                        f"changed={challenged.review.decision_changed} "
                        f"human_expected={ch_human}"
                    )
                    print(f"summary={challenged.review.summary!r}")
                    if _agrees(final_model, ch_human):
                        counters["challenge_converged"] += 1
                    else:
                        counters["challenge_failed"] += 1
                    _score(
                        model=final_model,
                        human=ch_human,
                        confidence=final_conf,
                        label="challenge",
                        counters=counters,
                    )
            else:
                # Single-pass cases (approve/reject/manual without follow-up).
                _score(
                    model=final_model,
                    human=final_human,
                    confidence=final_conf,
                    label="final",
                    counters=counters,
                )

    metrics = SEMANTIC_REVIEW_METRICS.snapshot()
    print(
        "\n=== promotion summary ===\n"
        f"compared={counters['compared']} "
        f"wrong_confident_e2e={counters['wrong_confident']} "
        f"unsafe_wrong_confident_initial={counters['unsafe_wrong_confident_initial']} "
        f"unsafe_wrong_confident_e2e={counters['unsafe_wrong']} "
        f"conservative_wrong_confident_e2e={counters['conservative_wrong']}\n"
        f"ambiguous_with_clarification={counters['ambiguous_with_clarification']} "
        f"ambiguous_missing_clarification={counters['ambiguous_missing_clarification']}\n"
        f"clarification_converged={counters['clarification_converged']} "
        f"clarification_failed={counters['clarification_failed']}\n"
        f"challenge_converged={counters['challenge_converged']} "
        f"challenge_failed={counters['challenge_failed']}\n"
        f"deterministic_ok={counters['deterministic_ok']} "
        f"deterministic_bad={counters['deterministic_bad']}\n"
        f"canonical_model_violation_approvals="
        f"{counters['canonical_model_violation_approvals']}\n"
        f"clarification_resolution_rate={metrics['clarification_resolution_rate']} "
        f"clarification_rounds_per_proposal={metrics['clarification_rounds_per_proposal']}"
    )

    promotion_ok = (
        counters["unsafe_wrong_confident_initial"] == 0
        and counters["ambiguous_missing_clarification"] == 0
        and counters["clarification_failed"] == 0
        and counters["challenge_failed"] == 0
        and counters["deterministic_bad"] == 0
        and counters["unsafe_wrong"] == 0
        and counters["canonical_model_violation_approvals"] == 0
    )
    print(f"promotion_ready={promotion_ok}")
    return 0 if promotion_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
