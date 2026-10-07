"""Phase 10 governed ontology proposal tests."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from semantic_memory.config import get_settings
from semantic_memory.exceptions import (
    OntologyCycleError,
    OntologyReuseRecommendedError,
    RevisionConflictError,
    UnauthorizedOperationError,
    UnknownClassError,
)
from semantic_memory.mcp.server import MCPPlaceholder
from semantic_memory.mcp.tools import OntologyMCPTools
from semantic_memory.models import (
    ActorStatus,
    ActorType,
    AliasTargetType,
    Cardinality,
    ConstraintType,
    OntologyChange,
    OntologyClass,
    OntologyClassParent,
    OntologyClassRevision,
    OntologyConstraint,
    ProposalStatus,
    ValueKind,
)
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES, Capability
from semantic_memory.repositories.actors import ActorRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import (
    ApplyProposalRequest,
    ProposalOutcome,
    ProposeAliasRequest,
    ProposeClassParentRequest,
    ProposeClassRequest,
    ProposeConstraintRequest,
    ProposePredicateRequest,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.ontology import OntologyService
from semantic_memory.services.proposals import ProposalService


def _ensure_proposer(session: Session, key: str = "proposer") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def _ensure_applier(session: Session, key: str = "applier") -> str:
    ActorRepository(session).create(
        key=key,
        actor_type=ActorType.SERVICE,
        status=ActorStatus.ACTIVE,
        capabilities=[
            Capability.ONTOLOGY_READ.value,
            Capability.ONTOLOGY_PROPOSE.value,
            Capability.ONTOLOGY_APPLY.value,
        ],
    )
    return key


def _envelope(actor_key: str) -> dict[str, object]:
    return {
        "actor_key": actor_key,
        "request_id": uuid.uuid4(),
        "idempotency_key": f"idem-{uuid.uuid4()}",
    }


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": get_settings().admin_api_token}


def test_propose_and_apply_class_creates_revision_and_change(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    applier = _ensure_applier(db_session)
    service = ProposalService(db_session)

    proposed = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="ResearchLab",
            label="Research Lab",
            parent_keys=["Organization"],
        )
    )
    assert proposed.outcome == ProposalOutcome.MANUAL_REVIEW
    assert proposed.proposal.status == ProposalStatus.IN_REVIEW
    assert any(item.gate_name == "final_deterministic" for item in proposed.proposal.gate_results)
    assert any(item.gate_name == "semantic_review" for item in proposed.proposal.gate_results)

    before_classes = db_session.scalar(select(func.count()).select_from(OntologyClass))
    applied = service.apply_proposal(
        ApplyProposalRequest(
            **_envelope(applier),
            proposal_id=proposed.proposal.id,
        )
    )
    assert applied.outcome == ProposalOutcome.APPLIED
    assert applied.proposal.status == ProposalStatus.ACCEPTED
    assert len(applied.proposal.changes) == 1
    assert applied.proposal.changes[0].new_revision_id is not None

    after_classes = db_session.scalar(select(func.count()).select_from(OntologyClass))
    assert after_classes == before_classes + 1
    created = OntologyService(db_session).get_class(class_key="ResearchLab")
    assert created.label == "Research Lab"
    assert "Organization" in created.parent_class_keys
    revisions = db_session.scalars(
        select(OntologyClassRevision).where(OntologyClassRevision.class_id == created.id)
    ).all()
    assert len(revisions) == 1
    changes = db_session.scalars(
        select(OntologyChange).where(OntologyChange.proposal_id == proposed.proposal.id)
    ).all()
    assert len(changes) == 1


def test_duplicate_class_key_recommends_reuse(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    result = ProposalService(db_session).propose_class(
        ProposeClassRequest(**_envelope(proposer), key="Person", label="Person Again")
    )
    assert result.outcome == ProposalOutcome.REUSE_RECOMMENDED
    assert result.proposal.status == ProposalStatus.REJECTED
    assert any(item.decision.value == "reuse_recommended" for item in result.proposal.gate_results)


def test_inheritance_cycle_is_rejected(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    applier = _ensure_applier(db_session)
    service = ProposalService(db_session)

    self_parent = service.propose_class(
        ProposeClassRequest(**_envelope(proposer), key="LoopClass", parent_keys=["LoopClass"])
    )
    assert self_parent.outcome == ProposalOutcome.REJECTED
    assert any(
        item.gate_name == "cycle" and item.decision.value == "fail"
        for item in self_parent.proposal.gate_results
    )

    created = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="CycleChild",
            parent_keys=["Organization"],
        )
    )
    assert created.outcome == ProposalOutcome.MANUAL_REVIEW
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=created.proposal.id)
    )
    # Organization already ancestors CycleChild; parenting Organization under it cycles.
    result = service.propose_class_parent(
        ProposeClassParentRequest(
            **_envelope(proposer),
            child_key="Organization",
            parent_key="CycleChild",
        )
    )
    assert result.outcome == ProposalOutcome.REJECTED
    assert any(
        item.gate_name == "cycle" and item.decision.value == "fail"
        for item in result.proposal.gate_results
    )


def test_domain_range_violations_are_rejected(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session)

    unknown_domain = service.propose_predicate(
        ProposePredicateRequest(
            **_envelope(proposer),
            key="worksAtUnknown",
            value_kind=ValueKind.ENTITY,
            cardinality=Cardinality.MANY,
            domain_keys=["NotARealClass"],
            range_keys=["Organization"],
        )
    )
    assert unknown_domain.outcome == ProposalOutcome.REJECTED
    assert any(
        item.gate_name == "domain_range" and item.decision.value == "fail"
        for item in unknown_domain.proposal.gate_results
    )

    missing_range = service.propose_predicate(
        ProposePredicateRequest(
            **_envelope(proposer),
            key="worksAtMissingRange",
            value_kind=ValueKind.ENTITY,
            cardinality=Cardinality.MANY,
            domain_keys=["Person"],
            range_keys=[],
        )
    )
    assert missing_range.outcome == ProposalOutcome.REJECTED


def test_rejected_proposal_leaves_ontology_unchanged(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    before = db_session.scalar(select(func.count()).select_from(OntologyClass))
    result = ProposalService(db_session).propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="BrokenChild",
            parent_keys=["DoesNotExist"],
        )
    )
    assert result.outcome == ProposalOutcome.REJECTED
    after = db_session.scalar(select(func.count()).select_from(OntologyClass))
    assert after == before
    with pytest.raises(UnknownClassError):
        OntologyService(db_session).get_class(class_key="BrokenChild")


def test_stale_revision_returns_revision_conflict(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    applier = _ensure_applier(db_session)
    service = ProposalService(db_session)

    create = service.propose_predicate(
        ProposePredicateRequest(
            **_envelope(proposer),
            key="mentors",
            value_kind=ValueKind.ENTITY,
            cardinality=Cardinality.MANY,
            domain_keys=["Person"],
            range_keys=["Person"],
        )
    )
    assert create.outcome == ProposalOutcome.MANUAL_REVIEW
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=create.proposal.id)
    )

    stale = service.propose_predicate(
        ProposePredicateRequest(
            **_envelope(proposer),
            key="mentors",
            value_kind=ValueKind.ENTITY,
            cardinality=Cardinality.ONE,
            domain_keys=["Person"],
            range_keys=["Person"],
            base_revision_number=0,
        )
    )
    assert stale.outcome == ProposalOutcome.MANUAL_REVIEW
    with pytest.raises(RevisionConflictError) as exc:
        service.apply_proposal(
            ApplyProposalRequest(**_envelope(applier), proposal_id=stale.proposal.id)
        )
    assert exc.value.error_code == "REVISION_CONFLICT"


def test_unauthorized_actor_cannot_apply(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session)
    proposed = service.propose_class(
        ProposeClassRequest(**_envelope(proposer), key="Faculty", parent_keys=["Person"])
    )
    assert proposed.outcome == ProposalOutcome.MANUAL_REVIEW
    with pytest.raises(UnauthorizedOperationError) as exc:
        service.apply_proposal(
            ApplyProposalRequest(**_envelope(proposer), proposal_id=proposed.proposal.id)
        )
    assert exc.value.error_code == "UNAUTHORIZED_OPERATION"
    with pytest.raises(UnknownClassError):
        OntologyService(db_session).get_class(class_key="Faculty")


def test_propose_alias_constraint_and_parent(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    applier = _ensure_applier(db_session)
    service = ProposalService(db_session)

    alias = service.propose_alias(
        ProposeAliasRequest(
            **_envelope(proposer),
            alias="human_being",
            target_type=AliasTargetType.CLASS,
            target_key="Person",
        )
    )
    assert alias.outcome == ProposalOutcome.MANUAL_REVIEW
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=alias.proposal.id)
    )
    assert OntologyService(db_session).get_class(alias="human_being").key == "Person"

    constraint = service.propose_constraint(
        ProposeConstraintRequest(
            **_envelope(proposer),
            key="person_name_required",
            constraint_type=ConstraintType.CUSTOM,
            expression={"requires": "displayName"},
        )
    )
    assert constraint.outcome == ProposalOutcome.MANUAL_REVIEW
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=constraint.proposal.id)
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OntologyConstraint)
            .where(OntologyConstraint.key == "person_name_required")
        )
        == 1
    )

    created = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key="Visitor",
            parent_keys=["Person"],
        )
    )
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=created.proposal.id)
    )
    visitor_row = OntologyRepository(db_session).get_class_by_key(
        namespace_key="core", class_key="Visitor"
    )
    assert visitor_row is not None
    revision = OntologyRepository(db_session).get_current_class_revision(visitor_row)
    assert revision is not None
    parent = service.propose_class_parent(
        ProposeClassParentRequest(
            **_envelope(proposer),
            child_key="Visitor",
            parent_key="Agent",
            base_revision_number=revision.revision_number,
        )
    )
    assert parent.outcome == ProposalOutcome.MANUAL_REVIEW
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=parent.proposal.id)
    )
    links = db_session.scalars(
        select(OntologyClassParent).where(OntologyClassParent.child_class_id == visitor_row.id)
    ).all()
    assert len(links) >= 2


def test_system_actor_can_apply_via_admin(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    service = ProposalService(db_session)
    proposed = service.propose_class(
        ProposeClassRequest(**_envelope(proposer), key="Clinic", parent_keys=["Organization"])
    )
    applied = service.apply_proposal(
        ApplyProposalRequest(**_envelope("system"), proposal_id=proposed.proposal.id)
    )
    assert applied.outcome == ProposalOutcome.APPLIED


def test_http_propose_and_apply_endpoints(client: TestClient) -> None:
    ensured = client.post(
        "/v1/actors/ensure",
        headers=_admin_headers(),
        json={
            "key": "http-proposer",
            "actor_type": "agent",
            "capabilities": [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            "status": "active",
        },
    )
    assert ensured.status_code == 200

    propose_body = {
        "actor_key": "http-proposer",
        "request_id": str(uuid.uuid4()),
        "idempotency_key": f"http-{uuid.uuid4()}",
        "key": "HttpLab",
        "parent_keys": ["Organization"],
    }
    proposed = client.post("/v1/ontology/proposals/classes", json=propose_body)
    assert proposed.status_code == 200
    body = proposed.json()
    assert body["outcome"] == "MANUAL_REVIEW"
    proposal_id = body["proposal"]["id"]

    fetched = client.get(f"/v1/ontology/proposals/{proposal_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == proposal_id

    denied = client.post(
        "/v1/ontology/proposals/apply",
        json={
            "actor_key": "http-proposer",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": f"http-apply-{uuid.uuid4()}",
            "proposal_id": proposal_id,
        },
    )
    assert denied.status_code == 403

    applied = client.post(
        "/v1/ontology/proposals/apply",
        json={
            "actor_key": "system",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": f"http-apply-ok-{uuid.uuid4()}",
            "proposal_id": proposal_id,
        },
    )
    assert applied.status_code == 200
    assert applied.json()["outcome"] == "APPLIED"


def test_apply_revalidates_against_current_ontology(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    applier = _ensure_applier(db_session)
    service = ProposalService(db_session)

    first = service.propose_class(
        ProposeClassRequest(**_envelope(proposer), key="SharedLab", parent_keys=["Organization"])
    )
    second = service.propose_class(
        ProposeClassRequest(**_envelope(proposer), key="SharedLab", parent_keys=["Organization"])
    )
    assert first.outcome == ProposalOutcome.MANUAL_REVIEW
    # Second propose sees the key as still free until first applies.
    assert second.outcome == ProposalOutcome.MANUAL_REVIEW

    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=first.proposal.id)
    )
    with pytest.raises(OntologyReuseRecommendedError) as exc:
        service.apply_proposal(
            ApplyProposalRequest(**_envelope(applier), proposal_id=second.proposal.id)
        )
    assert exc.value.error_code == "ONTOLOGY_REUSE_RECOMMENDED"


def test_apply_revalidates_cycles(db_session: Session) -> None:
    proposer = _ensure_proposer(db_session)
    applier = _ensure_applier(db_session)
    service = ProposalService(db_session)

    child = service.propose_class(
        ProposeClassRequest(**_envelope(proposer), key="CycleA", parent_keys=["Organization"])
    )
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=child.proposal.id)
    )

    # Propose parenting Organization under CycleA before the link exists → ready/manual.
    parent_link = service.propose_class_parent(
        ProposeClassParentRequest(
            **_envelope(proposer),
            child_key="Organization",
            parent_key="CycleA",
        )
    )
    assert parent_link.outcome == ProposalOutcome.REJECTED
    # Direct apply path: craft a submitted proposal that becomes cyclic after another apply.
    # Simulate by proposing a sibling link that later cycles via a second applied parent.
    a_to_b = service.propose_class(
        ProposeClassRequest(**_envelope(proposer), key="CycleB", parent_keys=["CycleA"])
    )
    service.apply_proposal(
        ApplyProposalRequest(**_envelope(applier), proposal_id=a_to_b.proposal.id)
    )
    # Propose CycleA -> CycleB before that parent exists; should fail cycle at propose.
    reverse = service.propose_class_parent(
        ProposeClassParentRequest(
            **_envelope(proposer),
            child_key="CycleA",
            parent_key="CycleB",
        )
    )
    assert reverse.outcome == ProposalOutcome.REJECTED
    assert any(item.gate_name == "cycle" for item in reverse.proposal.gate_results)
    # Ensure OntologyCycleError is reachable from revalidation helper.
    from semantic_memory.models.enums import ProposalType

    outcomes = service.gate_pipeline.revalidate_apply(
        proposal_type=ProposalType.CLASS_PARENT,
        payload={
            "namespace_key": "core",
            "child_key": "CycleA",
            "parent_key": "CycleB",
        },
    )
    assert any(item.gate_name == "cycle" and item.decision.value == "fail" for item in outcomes)
    with pytest.raises(OntologyCycleError):
        service._revalidate_before_apply(
            proposal_id=reverse.proposal.id,
            proposal_type=ProposalType.CLASS_PARENT,
            payload={
                "namespace_key": "core",
                "child_key": "CycleA",
                "parent_key": "CycleB",
            },
            request_id=uuid.uuid4(),
        )


def test_apply_proposal_dry_run_is_explicitly_projected(db_session: Session) -> None:
    """Dry-run apply must not look like a committed APPLIED response."""
    from semantic_memory.schemas.dry_run import OperationMode

    proposer = _ensure_proposer(db_session, "dry-apply-proposer")
    applier = _ensure_applier(db_session, "dry-apply-applier")
    service = ProposalService(db_session)
    suffix = uuid.uuid4().hex[:8]
    proposed = service.propose_class(
        ProposeClassRequest(
            **_envelope(proposer),
            key=f"DryRunLab{suffix}",
            label="Dry Run Lab",
            parent_keys=["Organization"],
        )
    )
    before_classes = db_session.scalar(select(func.count()).select_from(OntologyClass))
    preview = service.apply_proposal(
        ApplyProposalRequest(
            **_envelope(applier),
            proposal_id=proposed.proposal.id,
            dry_run=True,
        )
    )
    assert preview.dry_run is True
    assert preview.operation_mode == OperationMode.DRY_RUN
    assert preview.would_persist is True
    assert preview.projected is True
    assert preview.outcome == ProposalOutcome.WOULD_APPLY
    assert preview.proposal.status == ProposalStatus.ACCEPTED  # projected snapshot
    after_classes = db_session.scalar(select(func.count()).select_from(OntologyClass))
    assert after_classes == before_classes
    persisted = service.get_proposal(proposed.proposal.id)
    assert persisted.status == ProposalStatus.IN_REVIEW
    assert persisted.changes == []


def test_mcp_proposal_tools_expose_apply_ontology_proposal(db_session: Session) -> None:
    from semantic_memory.config import Settings

    tools = MCPPlaceholder.from_settings(Settings(mcp_tool_surface="all")).tools
    assert "propose_class" in tools
    assert "get_ontology_proposal" in tools
    assert "get_proposal" in tools  # advanced alias
    assert "apply_ontology_proposal" in tools
    assert "apply_proposal" not in tools

    proposer = _ensure_proposer(db_session, "mcp-proposer")
    adapter = OntologyMCPTools(db_session)
    result = adapter.propose_class(
        {
            "actor_key": proposer,
            "request_id": str(uuid.uuid4()),
            "idempotency_key": f"mcp-{uuid.uuid4()}",
            "key": "McpClass",
            "parent_keys": ["Thing"],
        }
    )
    assert result["outcome"] == "MANUAL_REVIEW"
    assert "error_code" not in result


def test_mcp_apply_ontology_proposal_commits_and_is_idempotent(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.orm import sessionmaker

    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("MCP_ACTOR_KEY", f"mcp-apply-actor-{suffix}")
    monkeypatch.setenv(
        "DEFAULT_ACTOR_CAPABILITIES",
        '["knowledge.read","knowledge.write","ontology.read","ontology.propose","ontology.apply","feedback.create"]',
    )
    get_settings.cache_clear()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        adapter = OntologyMCPTools(session)
        class_key = f"McpApplyClass{suffix}"
        proposed = adapter.propose_class(
            {
                "request_id": str(uuid.uuid4()),
                "idempotency_key": f"mcp-propose-{suffix}",
                "key": class_key,
                "parent_keys": ["Thing"],
            }
        )
        assert "error_code" not in proposed, proposed
        proposal_id = proposed["proposal"]["id"]

        apply_key = f"mcp-apply-{suffix}"
        applied = adapter.apply_ontology_proposal(
            {
                "proposal_id": proposal_id,
                "request_id": str(uuid.uuid4()),
                "idempotency_key": apply_key,
            }
        )
        assert applied.get("outcome") == "APPLIED", applied
        assert applied["proposal"]["status"] == "accepted"
        assert OntologyService(session).get_class(class_key=class_key) is not None

        retry = adapter.apply_ontology_proposal(
            {
                "proposal_id": proposal_id,
                "request_id": str(uuid.uuid4()),
                "idempotency_key": apply_key,
            }
        )
        assert retry.get("outcome") == "APPLIED", retry
        assert retry["proposal"]["id"] == proposal_id

        rejected = adapter.apply_ontology_proposal(
            {
                "proposal_id": proposal_id,
                "request_id": str(uuid.uuid4()),
                "idempotency_key": f"mcp-apply-again-{suffix}",
            }
        )
        assert rejected.get("error_code") == "VALIDATION_FAILED", rejected
    finally:
        session.close()
        get_settings.cache_clear()
