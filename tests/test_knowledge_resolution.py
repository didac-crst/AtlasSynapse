"""Phase D: resolution agenda, Claim-vs-domain routing, ontology necessity gate."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.models import ActorType, Entity, Source, Statement, StatementEvidence
from semantic_memory.models.enums import (
    AliasIdentityStrength,
    KnowledgeCandidateDependencyKind,
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    KnowledgeCandidateState,
    KnowledgeIngestionMode,
    KnowledgeIngestionPauseReason,
    KnowledgeIngestionStatus,
    ProposalStatus,
    ProposalType,
)
from semantic_memory.models.governance import OntologyProposal
from semantic_memory.models.llm_calls import LlmCallLog
from semantic_memory.repositories.provenance import ProvenanceRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import AddEntityAliasRequest, CreateEntityRequest
from semantic_memory.schemas.proposals import ApplyProposalRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.knowledge_extraction import KnowledgeExtractionService
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService
from semantic_memory.services.knowledge_resolution import KnowledgeResolutionService
from semantic_memory.services.knowledge_resolution.schemas import (
    BlockerType,
    CommitPath,
    ProjectionDisposition,
    ResolutionWarningCode,
)
from semantic_memory.services.proposals import ProposalService

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "knowledge_ingestion"
    / "strategy_package_v1.yaml"
)


def _actor_source(session: Session, key: str | None = None) -> tuple[str, uuid.UUID, uuid.UUID]:
    actor_key = key or f"resolve-{uuid.uuid4().hex[:8]}"
    actor = ActorService(session).ensure(
        ActorEnsureRequest(key=actor_key, actor_type=ActorType.AGENT)
    )
    source = Source(
        id=uuid.uuid4(),
        source_system="test",
        external_id=f"src-{uuid.uuid4().hex[:8]}",
        metadata_json={},
        created_by_actor_id=actor.id,
    )
    session.add(source)
    session.flush()
    return actor_key, actor.id, source.id


def _ingestion(
    session: Session,
    *,
    mode: KnowledgeIngestionMode = KnowledgeIngestionMode.EXECUTE,
    budgets: dict | None = None,
    content: str = "pkg",
) -> tuple[str, uuid.UUID]:
    actor_key, actor_id, source_id = _actor_source(session)
    rev = ProvenanceRepository(session).create_content_revision(
        source_id=source_id,
        revision_number=1,
        canonical_content=content,
        canonical_format="text",
        canonical_content_hash=hashlib.sha256(content.encode()).hexdigest(),
        captured_at=datetime.now(UTC),
        created_by_actor_id=actor_id,
    )
    service = KnowledgeIngestionService(session)
    row = service.create_ingestion(
        actor_id=actor_id,
        source_id=source_id,
        source_hash=rev.canonical_content_hash,
        request_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        pipeline_version="ki-resolve-v1",
        ontology_revision_marker="core@test",
        mode=mode,
        source_content_revision_id=rev.id,
        budgets_json=budgets,
    )
    service.set_ingestion_status(row.id, KnowledgeIngestionStatus.RESOLVING)
    return actor_key, row.id


def _add_candidate(
    session: Session,
    ingestion_id: uuid.UUID,
    *,
    key: str,
    kind: KnowledgeCandidateKind,
    polarity: KnowledgeCandidatePolarity = KnowledgeCandidatePolarity.POSITIVE,
    epistemic: KnowledgeCandidateEpistemicStatus = KnowledgeCandidateEpistemicStatus.ACTIVE,
    claim_text: str,
    claim_payload: dict | None = None,
) -> uuid.UUID:
    row = KnowledgeIngestionService(session).create_candidate(
        ingestion_id=ingestion_id,
        candidate_key=key,
        kind=kind,
        polarity=polarity,
        epistemic_status=epistemic,
        derivation=KnowledgeCandidateDerivation.EXPLICIT,
        claim_text=claim_text,
        claim_payload=claim_payload or {},
        state=KnowledgeCandidateState.EXTRACTED,
    )
    return row.id


def _entity_with_auth_alias(
    session: Session, *, actor_key: str, name: str, class_key: str = "Project"
) -> uuid.UUID:
    entities = EntityService(session)
    created = entities.create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=f"{name}-canon-{uuid.uuid4().hex[:6]}",
            class_key=class_key,
        )
    )
    assert created.entity is not None
    entities.add_entity_alias(
        AddEntityAliasRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entity_id=created.entity.id,
            alias=name,
            identity_strength=AliasIdentityStrength.AUTHORITATIVE,
        )
    )
    return created.entity.id


def _counts(session: Session) -> tuple[int, int, int]:
    entities = int(session.scalar(select(func.count()).select_from(Entity)) or 0)
    statements = int(session.scalar(select(func.count()).select_from(Statement)) or 0)
    evidence = int(session.scalar(select(func.count()).select_from(StatementEvidence)) or 0)
    return entities, statements, evidence


def test_rejected_hypothesis_claim_path_eligible(db_session: Session) -> None:
    _actor_key, ingestion_id = _ingestion(db_session)
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="hyp-1",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        epistemic=KnowledgeCandidateEpistemicStatus.REJECTED,
        claim_text="Open-ended knowledge is unique",
        claim_payload={"predicate_key_hint": "isUnique"},
    )
    before = _counts(db_session)
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    after = _counts(db_session)
    assert after == before
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["commit_path"] == CommitPath.CLAIM.value
    assert row.resolution_json["predicate"]["disposition"] == ProjectionDisposition.DESCRIPTIVE_HINT
    assert stats.eligible == 1
    assert stats.claim_path == 1


def test_claim_text_only_question_eligible(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session)
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="q-1",
        kind=KnowledgeCandidateKind.QUESTION,
        epistemic=KnowledgeCandidateEpistemicStatus.OPEN,
        claim_text="What recurring enterprise need would justify a commercial offering?",
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["commit_path"] == CommitPath.CLAIM.value


def test_negative_factual_assertion_claim_no_domain(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session)
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="neg-1",
        kind=KnowledgeCandidateKind.ASSERTION,
        polarity=KnowledgeCandidatePolarity.NEGATIVE,
        claim_text="AtlasSynapse is not a CRM",
        claim_payload={
            "subject": {"text": "AtlasSynapse"},
            "predicate_key_hint": "isA",
            "object": {"text": "CRM"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["commit_path"] == CommitPath.CLAIM.value
    assert row.resolution_json["epistemic_kind"] == "assertion"


def test_positive_assertion_existing_identities_and_predicate(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="AlphaCorp", class_key="Organization"
    )
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="BetaCorp", class_key="Organization"
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="pos-1",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="AlphaCorp relatedTo BetaCorp in the market",
        claim_payload={
            "subject": {"text": "AlphaCorp"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "BetaCorp"},
        },
    )
    before = _counts(db_session)
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    after = _counts(db_session)
    assert after == before
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["commit_path"] == CommitPath.DOMAIN_ASSERTION.value
    assert row.resolution_json["predicate"]["disposition"] == ProjectionDisposition.REUSE.value
    assert stats.domain_path == 1


def test_exact_predicate_key_reused_no_proposal(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="GammaOrg", class_key="Organization"
    )
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="DeltaOrg", class_key="Organization"
    )
    proposals_before = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="reuse-1",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="GammaOrg relatedTo DeltaOrg via partnership",
        claim_payload={
            "subject": {"text": "GammaOrg"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "DeltaOrg"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    proposals_after = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    assert proposals_after == proposals_before
    assert row.resolution_json["predicate"]["disposition"] == ProjectionDisposition.REUSE.value
    assert row.resolution_json["predicate"]["predicate_key"] == "relatedTo"


def test_substring_predicate_hit_not_auto_reused(db_session: Session) -> None:
    """ILIKE discovery of relatedTo for hint 'related' must not silently bind."""
    actor_key, ingestion_id = _ingestion(db_session)
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="SubOrgA", class_key="Organization"
    )
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="SubOrgB", class_key="Organization"
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="fuzzy-1",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="SubOrgA related SubOrgB as a loose textual relation",
        claim_payload={
            "subject": {"text": "SubOrgA"},
            "predicate_key_hint": "related",  # substring of relatedTo — discovery only
            "object": {"text": "SubOrgB"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    pred = row.resolution_json["predicate"]
    # Must not silently bind relatedTo from ILIKE.
    assert not (
        pred.get("disposition") == ProjectionDisposition.REUSE.value
        and pred.get("predicate_key") == "relatedTo"
        and pred.get("detail") == "search_reuse"
    )
    assert any(
        w["code"] == ResolutionWarningCode.POSSIBLE_REUSE_DISCOVERY.value
        for w in row.resolution_json["warnings"]
    ) or pred.get("disposition") in {
        ProjectionDisposition.PROPOSAL.value,
        ProjectionDisposition.OMIT.value,
        ProjectionDisposition.DESCRIPTIVE_HINT.value,
    }
    if pred.get("disposition") == ProjectionDisposition.REUSE.value:
        # Only allowed if ProposalService REUSE_RECOMMENDED resolved unambiguously.
        assert pred.get("detail") != "search_reuse"


def test_unknown_predicate_hint_inside_claim_no_ontology_proposal(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session)
    proposals_before = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="claim-pred",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Mercury may become strategically important",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "differentiatesThrough",
            "object": {"text": "governance"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    proposals_after = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    assert proposals_after == proposals_before
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["predicate"]["disposition"] == (
        ProjectionDisposition.DESCRIPTIVE_HINT.value
    )


def test_genuine_predicate_gap_proposes_and_blocks(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="ECorp", class_key="Organization")
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="FCorp", class_key="Organization")
    proposals_before = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="gap-1",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="ECorp competitivelyDifferentiates FCorp through semantic governance",
        claim_payload={
            "subject": {"text": "ECorp"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "FCorp"},
        },
    )
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    proposals_after = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    assert proposals_after == proposals_before + 1
    assert row.state == KnowledgeCandidateState.BLOCKED.value
    assert row.resolution_json["commit_path"] == CommitPath.DOMAIN_ASSERTION.value
    assert row.resolution_json["predicate"]["disposition"] == ProjectionDisposition.PROPOSAL.value
    assert row.resolution_json["ontology_proposal_ids"]
    assert any(b["type"] == BlockerType.ONTOLOGY_PROPOSAL.value for b in row.blockers_json)
    prop = db_session.get(
        OntologyProposal, uuid.UUID(row.resolution_json["ontology_proposal_ids"][0])
    )
    assert prop is not None
    assert prop.status != ProposalStatus.ACCEPTED.value
    assert stats.ontology_proposals_created == 1
    assert stats.blocked == 1


def test_dry_run_would_propose_no_durable_proposal(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session, mode=KnowledgeIngestionMode.DRY_RUN)
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="GCorp", class_key="Organization")
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="HCorp", class_key="Organization")
    proposals_before = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="dry-gap",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="GCorp competitivelyDifferentiates HCorp in adjacent markets",
        claim_payload={
            "subject": {"text": "GCorp"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "HCorp"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    proposals_after = int(
        db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0
    )
    assert proposals_after == proposals_before
    assert row.state == KnowledgeCandidateState.BLOCKED.value
    assert any(
        w["code"] == ResolutionWarningCode.WOULD_PROPOSE_ONTOLOGY.value
        for w in row.resolution_json["warnings"]
    )
    assert row.resolution_json["would_propose_ontology"]


def test_required_ambiguous_identity_blocked(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    # Supporting name match only → AMBIGUOUS under identity governance.
    EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Mercury",
            class_key="Project",
        )
    )
    EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Venus",
            class_key="Project",
        )
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="amb-req",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="Mercury relatedTo Venus as sibling planets",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Venus"},
        },
    )
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.BLOCKED.value
    assert any(b["type"] == BlockerType.IDENTITY_CLARIFICATION.value for b in row.blockers_json)
    assert stats.identity_blockers >= 1


def test_optional_ambiguous_claim_projection_omitted_still_eligible(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Mercury",
            class_key="Project",
        )
    )
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="amb-opt",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Mercury may become strategically important",
        claim_payload={"subject": {"text": "Mercury"}},
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["subject"]["disposition"] == ProjectionDisposition.OMIT.value
    assert any(
        w["code"] == ResolutionWarningCode.AMBIGUOUS_OPTIONAL_PROJECTION.value
        for w in row.resolution_json["warnings"]
    )


def test_safe_new_identity_future_create_no_entity(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="KnownOrg", class_key="Organization"
    )
    before = _counts(db_session)
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="create-plan",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="BrandNewOrgZZ relatedTo KnownOrg as a partner",
        claim_payload={
            "subject": {"text": "BrandNewOrgZZ"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "KnownOrg"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    after = _counts(db_session)
    assert after == before
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["subject"]["disposition"] == ProjectionDisposition.CREATE.value


def test_dependency_child_wakes_after_parent(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session)
    parent = _add_candidate(
        db_session,
        ingestion_id,
        key="parent",
        kind=KnowledgeCandidateKind.QUESTION,
        epistemic=KnowledgeCandidateEpistemicStatus.OPEN,
        claim_text="What is the parent question?",
    )
    child = _add_candidate(
        db_session,
        ingestion_id,
        key="child",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Child hypothesis depends on parent",
    )
    KnowledgeIngestionService(db_session).add_dependency(
        parent_candidate_id=parent,
        child_candidate_id=child,
        dependency_kind=KnowledgeCandidateDependencyKind.REQUIRES_RESOLUTION,
    )
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    ki = KnowledgeIngestionService(db_session)
    assert ki.get_candidate(parent).state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert ki.get_candidate(child).state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert stats.dependents_woken >= 1


def test_budget_exhaustion_pauses_ingestion(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session, budgets={"max_candidate_attempts": 1})
    _add_candidate(
        db_session,
        ingestion_id,
        key="b1",
        kind=KnowledgeCandidateKind.QUESTION,
        epistemic=KnowledgeCandidateEpistemicStatus.OPEN,
        claim_text="Question one?",
    )
    _add_candidate(
        db_session,
        ingestion_id,
        key="b2",
        kind=KnowledgeCandidateKind.QUESTION,
        epistemic=KnowledgeCandidateEpistemicStatus.OPEN,
        claim_text="Question two?",
    )
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    ingestion = KnowledgeIngestionService(db_session).get_ingestion(ingestion_id)
    assert stats.budget_exhausted is True
    assert ingestion.status == KnowledgeIngestionStatus.PAUSED.value
    assert ingestion.pause_reason == KnowledgeIngestionPauseReason.BUDGET_EXHAUSTED.value
    states = {c.state for c in KnowledgeIngestionService(db_session).list_candidates(ingestion_id)}
    assert KnowledgeCandidateState.ACTIONABLE.value in states or (
        KnowledgeCandidateState.EXTRACTED.value in states
    )


def test_rerun_idempotent_no_duplicate_proposals(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="ICorp", class_key="Organization")
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="JCorp", class_key="Organization")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="idem-gap",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="ICorp competitivelyDifferentiates JCorp across product lines",
        claim_payload={
            "subject": {"text": "ICorp"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "JCorp"},
        },
    )
    svc = KnowledgeResolutionService(db_session)
    svc.resolve_ingestion(ingestion_id)
    first = KnowledgeIngestionService(db_session).get_candidate(cid)
    proposals_mid = int(db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0)
    svc.resolve_ingestion(ingestion_id)
    second = KnowledgeIngestionService(db_session).get_candidate(cid)
    proposals_end = int(db_session.scalar(select(func.count()).select_from(OntologyProposal)) or 0)
    assert proposals_end == proposals_mid
    assert (
        first.resolution_json["ontology_proposal_ids"]
        == second.resolution_json["ontology_proposal_ids"]
    )
    assert first.state == second.state == KnowledgeCandidateState.BLOCKED.value


def test_externally_satisfied_ontology_blocker_clears(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="KCorp", class_key="Organization")
    _entity_with_auth_alias(db_session, actor_key=actor_key, name="LCorp", class_key="Organization")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="ext-ont",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="KCorp competitivelyDifferentiates LCorp through process excellence",
        claim_payload={
            "subject": {"text": "KCorp"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "LCorp"},
        },
    )
    svc = KnowledgeResolutionService(db_session)
    svc.resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.BLOCKED.value
    proposal_id = uuid.UUID(row.resolution_json["ontology_proposal_ids"][0])

    # Apply via governance (external satisfaction).
    ProposalService(db_session).apply_proposal(
        ApplyProposalRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            proposal_id=proposal_id,
        )
    )
    svc.resolve_ingestion(ingestion_id)
    again = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert again.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert again.resolution_json["predicate"]["disposition"] == ProjectionDisposition.REUSE.value
    assert again.blockers_json == []


def test_dependency_cycle_marks_stall_blocker(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session)
    a = _add_candidate(
        db_session,
        ingestion_id,
        key="cycle-a",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Cycle candidate A",
    )
    b = _add_candidate(
        db_session,
        ingestion_id,
        key="cycle-b",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Cycle candidate B",
    )
    ki = KnowledgeIngestionService(db_session)
    ki.add_dependency(
        parent_candidate_id=a,
        child_candidate_id=b,
        dependency_kind=KnowledgeCandidateDependencyKind.REQUIRES_RESOLUTION,
    )
    ki.add_dependency(
        parent_candidate_id=b,
        child_candidate_id=a,
        dependency_kind=KnowledgeCandidateDependencyKind.REQUIRES_RESOLUTION,
    )
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    assert stats.dependency_stall is True
    assert stats.candidates_attempted == 0
    for cid in (a, b):
        row = ki.get_candidate(cid)
        assert row.state == KnowledgeCandidateState.BLOCKED.value
        assert any(
            b["type"] == BlockerType.DEPENDENCY.value and b["detail"] == "dependency_cycle_or_stall"
            for b in row.blockers_json
        )
    ingestion = ki.get_ingestion(ingestion_id)
    assert ingestion.status == KnowledgeIngestionStatus.RESOLVING.value
    assert ingestion.stats_json["resolution"]["dependency_stall"] is True


def test_llm_budget_includes_proposal_semantic_review(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session, budgets={"max_llm_calls": 1})
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="LlmOrgA", class_key="Organization"
    )
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="LlmOrgB", class_key="Organization"
    )
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="LlmOrgC", class_key="Organization"
    )
    _entity_with_auth_alias(
        db_session, actor_key=actor_key, name="LlmOrgD", class_key="Organization"
    )
    llm_before = int(db_session.scalar(select(func.count()).select_from(LlmCallLog)) or 0)
    _add_candidate(
        db_session,
        ingestion_id,
        key="llm-gap-1",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="LlmOrgA competitivelyDifferentiates LlmOrgB through review budget one",
        claim_payload={
            "subject": {"text": "LlmOrgA"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "LlmOrgB"},
        },
    )
    _add_candidate(
        db_session,
        ingestion_id,
        key="llm-gap-2",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="LlmOrgC competitivelyDifferentiates LlmOrgD through review budget two",
        claim_payload={
            "subject": {"text": "LlmOrgC"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "LlmOrgD"},
        },
    )
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    llm_after = int(db_session.scalar(select(func.count()).select_from(LlmCallLog)) or 0)
    assert llm_after > llm_before
    assert stats.llm_calls >= 1
    ingestion = KnowledgeIngestionService(db_session).get_ingestion(ingestion_id)
    assert stats.budget_exhausted is True
    assert ingestion.status == KnowledgeIngestionStatus.PAUSED.value
    assert ingestion.pause_reason == KnowledgeIngestionPauseReason.BUDGET_EXHAUSTED.value


def test_positive_assertion_insufficient_structure_warning(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session)
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="struct-1",
        kind=KnowledgeCandidateKind.ASSERTION,
        claim_text="AtlasSynapse differentiates through semantic governance",
        claim_payload={"subject": {"text": "AtlasSynapse"}},  # missing pred/object
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["commit_path"] == CommitPath.CLAIM.value
    assert any(
        w["code"] == ResolutionWarningCode.INSUFFICIENT_STRUCTURE_FOR_DOMAIN_ASSERTION.value
        for w in row.resolution_json["warnings"]
    )


def test_strategy_package_resolution_counts(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    _, ingestion_id = _ingestion(db_session, content=content)
    KnowledgeExtractionService(db_session).extract(ingestion_id)
    before = _counts(db_session)
    stats = KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    after = _counts(db_session)
    assert after == before

    candidates = KnowledgeIngestionService(db_session).list_candidates(ingestion_id)
    eligible = sum(
        1 for c in candidates if c.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    )
    blocked = sum(1 for c in candidates if c.state == KnowledgeCandidateState.BLOCKED.value)
    claim_path = sum(
        1 for c in candidates if (c.resolution_json or {}).get("commit_path") == "claim"
    )
    domain_path = sum(
        1 for c in candidates if (c.resolution_json or {}).get("commit_path") == "domain_assertion"
    )
    identity_blockers = sum(
        1
        for c in candidates
        for b in (c.blockers_json or [])
        if isinstance(b, dict) and b.get("type") == BlockerType.IDENTITY_CLARIFICATION.value
    )
    proposals = int(
        db_session.scalar(
            select(func.count())
            .select_from(OntologyProposal)
            .where(OntologyProposal.proposal_type == ProposalType.PREDICATE.value)
        )
        or 0
    )

    # Most strategy candidates are epistemic → Claim; nearly all should be eligible.
    assert eligible >= 10
    assert claim_path >= eligible - 1
    assert stats.eligible == eligible
    assert stats.blocked == blocked
    structure_warnings = sum(
        1
        for c in candidates
        for w in (c.resolution_json or {}).get("warnings", [])
        if isinstance(w, dict)
        and w.get("code") == ResolutionWarningCode.INSUFFICIENT_STRUCTURE_FOR_DOMAIN_ASSERTION.value
    )
    # Positive textual assertions without full SPO should be auditable.
    assert structure_warnings >= 1
    assert (
        f"eligible={eligible} blocked={blocked} claim={claim_path} "
        f"domain={domain_path} identity_blockers={identity_blockers} "
        f"proposals_created={stats.ontology_proposals_created} proposals_total={proposals} "
        f"structure_warnings={structure_warnings}"
    )


def test_resolution_zero_live_knowledge_writes(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    _, ingestion_id = _ingestion(db_session, content=content)
    KnowledgeExtractionService(db_session).extract(ingestion_id)
    before = _counts(db_session)
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    after = _counts(db_session)
    assert after == before
    effects = KnowledgeIngestionService(db_session).list_effects(ingestion_id)
    assert effects == []
