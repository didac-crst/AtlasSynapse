"""Deterministic ontology key identity and shape gates."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import Cardinality, ValueKind
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import (
    ProposalOutcome,
    ProposeClassRequest,
    ProposePredicateRequest,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import MockSemanticReviewer, ReviewDecision


def _ensure(session: Session, key: str = "key-shape-proposer") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def _env(actor: str) -> dict[str, object]:
    return {
        "actor_key": actor,
        "request_id": uuid.uuid4(),
        "idempotency_key": f"idem-{uuid.uuid4()}",
    }


def test_lowercase_person_reuses_core_person(db_session: Session) -> None:
    actor = _ensure(db_session)
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            **_env(actor),
            key="person",
            description="A human being.",
            parent_keys=["Thing"],
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    assert result.outcome == ProposalOutcome.REUSE_RECOMMENDED
    existing = next(g for g in result.proposal.gate_results if g.gate_name == "existing_key")
    assert existing.details.get("reason") == "canonical_key_equivalent"
    assert existing.details.get("existing_key") == "Person"


def test_bad_predicate_shape_is_rejected(db_session: Session) -> None:
    actor = _ensure(db_session, "pred-shape")
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_predicate(
        ProposePredicateRequest(
            **_env(actor),
            key="SpouseOf",
            description="Marriage relation between persons.",
            value_kind=ValueKind.ENTITY,
            cardinality=Cardinality.MANY,
            domain_keys=["Person"],
            range_keys=["Person"],
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    assert result.outcome == ProposalOutcome.REJECTED
    shape = next(g for g in result.proposal.gate_results if g.gate_name == "key_shape")
    assert shape.decision.value == "fail"
    assert shape.details.get("suggested_key") == "spouseOf"
