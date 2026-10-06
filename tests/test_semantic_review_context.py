"""Regression tests for relevance-ranked semantic review context selection."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.config import Settings
from semantic_memory.models import ActorType
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import Cardinality, ProposalType, ValueKind
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import ProposeClassRequest
from semantic_memory.seeding.calibration_ontology import ensure_calibration_ontology
from semantic_memory.services.actors import ActorService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import MockSemanticReviewer, ReviewDecision
from semantic_memory.services.review_context import SemanticReviewContextBuilder


def _ensure_calibration(session: Session) -> None:
    ensure_calibration_ontology(session)
    session.flush()


def test_job_title_context_contains_role(db_session: Session) -> None:
    _ensure_calibration(db_session)
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.CLASS,
        payload={
            "namespace_key": "core",
            "key": "JobTitle",
            "label": "Job Title",
            "description": "A position or title held by a person in an organization.",
            "parent_keys": ["Thing"],
        },
    )
    keys = ctx.concept_keys()
    assert "class:Role" in keys
    assert any(t["key"] == "Role" for t in ctx.candidate_selection_trace)
    # Role must outrank or survive ahead of generic Thing-children eviction.
    role_idx = keys.index("class:Role")
    assert role_idx < 6


def test_responsibility_and_position_label_include_role(db_session: Session) -> None:
    _ensure_calibration(db_session)
    for key, description in (
        # Underspecified eval case must still surface Role via peer ranking.
        ("Responsibility", "A duty or obligation (initially underspecified)."),
        ("Responsibility", "A duty or obligation attached to a role."),
        (
            "PositionLabel",
            "Another name for an organizational role or job position.",
        ),
    ):
        ctx = SemanticReviewContextBuilder(db_session).build(
            proposal_type=ProposalType.CLASS,
            payload={
                "namespace_key": "core",
                "key": key,
                "description": description,
                "parent_keys": ["Thing"],
            },
        )
        assert "class:Role" in ctx.concept_keys(), f"{key}: {description}"


def test_relies_upon_context_contains_depends_on(db_session: Session) -> None:
    _ensure_calibration(db_session)
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.PREDICATE,
        payload={
            "namespace_key": "core",
            "key": "reliesUpon",
            "label": "relies upon",
            "description": "Indicates that one entity depends on another entity to function.",
            "value_kind": "entity",
            "cardinality": "many",
            "domain_keys": ["Thing"],
            "range_keys": ["Thing"],
        },
    )
    keys = ctx.concept_keys()
    assert "predicate:dependsOn" in keys
    depends_idx = keys.index("predicate:dependsOn")
    # dependsOn should rank above broad/generic peers when present.
    if "predicate:relatedTo" in keys:
        assert depends_idx < keys.index("predicate:relatedTo")
    if "predicate:description" in keys:
        assert depends_idx < keys.index("predicate:description")


def test_generic_thing_children_cannot_evict_stronger_candidate(db_session: Session) -> None:
    _ensure_calibration(db_session)
    ctx = SemanticReviewContextBuilder(
        db_session,
        Settings(semantic_review_max_candidates=5),
    ).build(
        proposal_type=ProposalType.CLASS,
        payload={
            "namespace_key": "core",
            "key": "JobTitle",
            "description": "A position or title held by a person in an organization.",
            "parent_keys": ["Thing"],
        },
    )
    keys = ctx.concept_keys()
    assert "class:Role" in keys
    assert len(keys) <= 5
    # With a tight cap, Role must survive; alphabetical Activity/Document must not displace it.
    assert "class:Role" in keys


def test_deterministic_fail_still_skips_llm(db_session: Session) -> None:
    _ensure_calibration(db_session)
    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="ctx-proposer",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    calls: list[str] = []

    class CountingReviewer:
        provider = "mock"
        model = "counting"

        def review(self, request):  # type: ignore[no-untyped-def]
            calls.append("called")
            return MockSemanticReviewer().review(request)

    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="shadow"),
        reviewer=CountingReviewer(),
    )
    result = service.propose_class(
        ProposeClassRequest(
            actor_key="ctx-proposer",
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="Broken",
            parent_keys=["MissingParent"],
            metadata={"review_decision": ReviewDecision.APPROVE.value},
        )
    )
    assert result.outcome.value == "REJECTED"
    assert calls == []
    semantic = next(g for g in result.proposal.gate_results if g.gate_name == "semantic_review")
    assert semantic.details.get("skipped") is True


def test_tiny_forced_budget_degrades_to_manual_review(db_session: Session) -> None:
    _ensure_calibration(db_session)
    # Config floor is 200 tokens; force an over-budget context object directly.
    from semantic_memory.services.review import ExternalSemanticReviewer, ReviewRequest
    from semantic_memory.services.review_context import ReviewContext

    ctx = ReviewContext(
        proposal_type=ProposalType.CLASS,
        proposal={"namespace_key": "core", "key": "ThinContextProbe"},
        concepts=[],
        estimated_tokens=10_000,
        budget_exceeded=True,
    )
    result = ExternalSemanticReviewer(
        Settings(semantic_review_mode="shadow", semantic_review_api_key="dummy")
    ).review(
        ReviewRequest(
            proposal_type=ProposalType.CLASS,
            payload={"namespace_key": "core", "key": "X"},
            context=ctx,
        )
    )
    assert result.decision == ReviewDecision.MANUAL_REVIEW
    assert result.details.get("reason") == "semantic_context_budget_exceeded"


def test_candidate_selection_trace_present(db_session: Session) -> None:
    _ensure_calibration(db_session)
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.PREDICATE,
        payload={
            "namespace_key": "core",
            "key": "reliesUpon",
            "description": "Indicates that one entity depends on another entity to function.",
            "value_kind": ValueKind.ENTITY.value,
            "cardinality": Cardinality.MANY.value,
            "domain_keys": ["Thing"],
            "range_keys": ["Thing"],
        },
    )
    assert ctx.candidate_selection_trace
    depends = next(t for t in ctx.candidate_selection_trace if t["key"] == "dependsOn")
    assert "lexical" in depends["trace"] or depends["tier"] in {"pinned", "supporting"}


def test_employed_by_gets_canonical_derivation_hints(db_session: Session) -> None:
    _ensure_calibration(db_session)
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.PREDICATE,
        payload={
            "namespace_key": "core",
            "key": "employedBy",
            "description": "Direct Person→Organization employment link.",
            "value_kind": ValueKind.ENTITY.value,
            "cardinality": Cardinality.MANY.value,
            "domain_keys": ["Person"],
            "range_keys": ["Organization"],
        },
    )
    block = ctx.to_prompt_block()
    assert "CANONICAL DERIVATION HINTS:" in block
    assert "holdsRole" in block
    assert "roleAt" in block
    assert "predicate:holdsRole" in ctx.concept_keys()
    assert "predicate:roleAt" in ctx.concept_keys()
    assert any("canonical_derivation" == t["trace"] for t in ctx.candidate_selection_trace)


def test_verified_skill_gets_provenance_hint(db_session: Session) -> None:
    _ensure_calibration(db_session)
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.CLASS,
        payload={
            "namespace_key": "core",
            "key": "VerifiedSkill",
            "description": "A Skill that has been verified by evidence or a trusted source.",
            "parent_keys": ["Thing"],
        },
    )
    assert any(
        "provenance" in h.casefold() or "evidence" in h.casefold() for h in ctx.derivation_hints
    )
    assert "class:Skill" in ctx.concept_keys()
    assert ctx.prefer_clarification is False


def test_professional_skill_prefers_clarification_not_reuse(db_session: Session) -> None:
    """Composition-vs-primitive residual: do not accept confident Skill reuse."""
    _ensure_calibration(db_session)
    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="composition-proposer",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.CLASS,
        payload={
            "namespace_key": "core",
            "key": "ProfessionalSkill",
            "description": "A skill used in a professional Role context.",
            "parent_keys": ["Thing"],
        },
    )
    assert ctx.prefer_clarification is True
    assert "class:Skill" in ctx.concept_keys()

    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            actor_key="composition-proposer",
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="ProfessionalSkill",
            description="A skill used in a professional Role context.",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REUSE_EXISTING.value,
                "review_confidence": 0.93,
                "related_existing_concepts": [
                    {
                        "kind": "class",
                        "key": "Skill",
                        "reason": "Covered by Skill plus role context",
                    }
                ],
            },
        )
    )
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert review.decision.value == "manual_review"
    assert result.open_clarification_request is not None


def test_verified_skill_confident_reuse_not_redirected_to_clarification(
    db_session: Session,
) -> None:
    """Metadata-as-ontology stays reject/reuse; not a prefer_clarification case."""
    _ensure_calibration(db_session)
    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="verified-proposer",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            actor_key="verified-proposer",
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="VerifiedSkill",
            description="A Skill that has been verified by evidence or a trusted source.",
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REUSE_EXISTING.value,
                "review_confidence": 0.94,
                "related_existing_concepts": [
                    {"kind": "class", "key": "Skill", "reason": "Verification is metadata"}
                ],
            },
        )
    )
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert review.decision.value == "reuse_existing"
    assert result.open_clarification_request is None


def test_skill_grouping_distinct_approve_not_forced_to_clarification(
    db_session: Session,
) -> None:
    """Clear distinct grouping concept may approve; not prefer_clarification."""
    _ensure_calibration(db_session)
    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="grouping-proposer",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.CLASS,
        payload={
            "namespace_key": "core",
            "key": "SkillGrouping",
            "label": "Skill",
            "description": (
                "A strategic domain grouping of competencies, not a concrete agent-held competence."
            ),
            "parent_keys": ["Thing"],
        },
    )
    assert ctx.prefer_clarification is False

    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            actor_key="grouping-proposer",
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="SkillGrouping",
            label="Skill",
            description=(
                "A strategic domain grouping of competencies, not a concrete agent-held competence."
            ),
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.APPROVE.value,
                "review_confidence": 0.94,
                "related_existing_concepts": [
                    {"kind": "class", "key": "Skill", "reason": "Related but distinct"}
                ],
            },
        )
    )
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert review.decision.value == "approve"


def test_historical_role_confident_reuse_not_redirected_to_clarification(
    db_session: Session,
) -> None:
    """Temporal metadata leak stays reject/reuse; not prefer_clarification."""
    _ensure_calibration(db_session)
    ActorService(db_session).ensure(
        ActorEnsureRequest(
            key="historical-proposer",
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    ctx = SemanticReviewContextBuilder(db_session).build(
        proposal_type=ProposalType.CLASS,
        payload={
            "namespace_key": "core",
            "key": "HistoricalRole",
            "description": (
                "A Role that was held in the past. Used to distinguish former "
                "roles from current ones."
            ),
            "parent_keys": ["Thing"],
        },
    )
    assert ctx.prefer_clarification is False

    service = ProposalService(db_session, reviewer=MockSemanticReviewer())
    result = service.propose_class(
        ProposeClassRequest(
            actor_key="historical-proposer",
            request_id=uuid.uuid4(),
            idempotency_key=f"idem-{uuid.uuid4()}",
            key="HistoricalRole",
            description=(
                "A Role that was held in the past. Used to distinguish former "
                "roles from current ones."
            ),
            parent_keys=["Thing"],
            metadata={
                "review_decision": ReviewDecision.REUSE_EXISTING.value,
                "review_confidence": 0.95,
                "related_existing_concepts": [
                    {
                        "kind": "class",
                        "key": "Role",
                        "reason": "Temporality belongs on statements",
                    }
                ],
            },
        )
    )
    review = result.proposal.effective_semantic_review
    assert review is not None
    assert review.decision.value == "reuse_existing"
    assert result.open_clarification_request is None
