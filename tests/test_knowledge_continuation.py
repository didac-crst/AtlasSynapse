"""Phase E: clarification planner, answers, continue_ingestion."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semantic_memory.exceptions import InvalidStateTransitionError, ValidationFailedError
from semantic_memory.models import ActorType, Entity, Source, Statement, StatementEvidence
from semantic_memory.models.enums import (
    AliasIdentityStrength,
    EntityStatus,
    KnowledgeCandidateDependencyKind,
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    KnowledgeCandidateState,
    KnowledgeIngestionClarificationKind,
    KnowledgeIngestionClarificationStatus,
    KnowledgeIngestionMode,
    KnowledgeIngestionPauseReason,
    KnowledgeIngestionStatus,
)
from semantic_memory.models.operations import WriteClarificationRequest
from semantic_memory.repositories.provenance import ProvenanceRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import AddEntityAliasRequest, CreateEntityRequest
from semantic_memory.schemas.proposals import ApplyProposalRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.knowledge_continuation import (
    ClarificationAnswerInput,
    IdentityAnswerResolution,
    KnowledgeContinuationService,
)
from semantic_memory.services.knowledge_extraction import KnowledgeExtractionService
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService
from semantic_memory.services.knowledge_resolution import KnowledgeResolutionService
from semantic_memory.services.knowledge_resolution.schemas import ProjectionDisposition
from semantic_memory.services.proposals import ProposalService

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "knowledge_ingestion"
    / "strategy_package_v1.yaml"
)


def _actor_source(session: Session) -> tuple[str, uuid.UUID, uuid.UUID]:
    actor_key = f"cont-{uuid.uuid4().hex[:8]}"
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
) -> tuple[str, uuid.UUID]:
    actor_key, actor_id, source_id = _actor_source(session)
    rev = ProvenanceRepository(session).create_content_revision(
        source_id=source_id,
        revision_number=1,
        canonical_content="pkg",
        canonical_format="text",
        canonical_content_hash=hashlib.sha256(b"pkg").hexdigest(),
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
        pipeline_version="ki-cont-v1",
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
    kind: KnowledgeCandidateKind = KnowledgeCandidateKind.ASSERTION,
    polarity: KnowledgeCandidatePolarity = KnowledgeCandidatePolarity.POSITIVE,
    claim_text: str,
    claim_payload: dict | None = None,
) -> uuid.UUID:
    row = KnowledgeIngestionService(session).create_candidate(
        ingestion_id=ingestion_id,
        candidate_key=key,
        kind=kind,
        polarity=polarity,
        epistemic_status=KnowledgeCandidateEpistemicStatus.ACTIVE,
        derivation=KnowledgeCandidateDerivation.EXPLICIT,
        claim_text=claim_text,
        claim_payload=claim_payload or {},
        state=KnowledgeCandidateState.EXTRACTED,
    )
    return row.id


def _name_only_entity(session: Session, *, actor_key: str, name: str) -> uuid.UUID:
    """Supporting name match only → IdentityService AMBIGUOUS on resolve."""
    created = EntityService(session).create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=name,
            class_key="Organization",
        )
    )
    assert created.entity is not None
    return created.entity.id


def _auth_alias(session: Session, *, actor_key: str, name: str) -> uuid.UUID:
    entities = EntityService(session)
    created = entities.create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=f"{name}-canon-{uuid.uuid4().hex[:6]}",
            class_key="Organization",
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
    return (
        int(session.scalar(select(func.count()).select_from(Entity)) or 0),
        int(session.scalar(select(func.count()).select_from(Statement)) or 0),
        int(session.scalar(select(func.count()).select_from(StatementEvidence)) or 0),
    )


def _resolve_and_plan(session: Session, ingestion_id: uuid.UUID):
    KnowledgeResolutionService(session).resolve_ingestion(ingestion_id)
    return KnowledgeContinuationService(session).plan_clarifications(ingestion_id)


def test_required_identity_blocker_creates_package_clarification(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _name_only_entity(db_session, actor_key=actor_key, name="Venus")
    _add_candidate(
        db_session,
        ingestion_id,
        key="amb",
        claim_text="Mercury relatedTo Venus as sibling orgs",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Venus"},
        },
    )
    before_wcr = int(
        db_session.scalar(select(func.count()).select_from(WriteClarificationRequest)) or 0
    )
    result = _resolve_and_plan(db_session, ingestion_id)
    after_wcr = int(
        db_session.scalar(select(func.count()).select_from(WriteClarificationRequest)) or 0
    )
    assert after_wcr == before_wcr
    assert len(result.open_clarifications) >= 1
    identity = [c for c in result.open_clarifications if c.clarification_kind == "identity"]
    assert identity
    payload = identity[0].question_payload
    assert payload["field"] in {"subject", "object"}
    assert payload["allowed_resolutions"] == ["chosen_entity", "create_new", "reject"]
    assert payload["candidate_entities"]


def test_optional_claim_ambiguity_creates_no_clarification(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _add_candidate(
        db_session,
        ingestion_id,
        key="opt",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Mercury may become strategically important",
        claim_payload={"subject": {"text": "Mercury"}},
    )
    result = _resolve_and_plan(db_session, ingestion_id)
    assert result.open_clarifications == []
    cand = KnowledgeIngestionService(db_session).list_candidates(ingestion_id)[0]
    assert cand.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value


def test_planner_rerun_reuses_same_clarification_row(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _name_only_entity(db_session, actor_key=actor_key, name="Venus")
    _add_candidate(
        db_session,
        ingestion_id,
        key="amb",
        claim_text="Mercury relatedTo Venus for planner idempotency",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Venus"},
        },
    )
    first = _resolve_and_plan(db_session, ingestion_id)
    second = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    assert {c.clarification_id for c in first.open_clarifications} == {
        c.clarification_id for c in second.open_clarifications
    }
    assert {c.clarification_key for c in first.open_clarifications} == {
        c.clarification_key for c in second.open_clarifications
    }


def test_changed_identity_set_supersedes_prior_clarification(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _name_only_entity(db_session, actor_key=actor_key, name="Venus")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="amb",
        claim_text="Mercury relatedTo Venus before third candidate arrives",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Venus"},
        },
    )
    first = _resolve_and_plan(db_session, ingestion_id)
    subject_clar = next(
        c for c in first.open_clarifications if c.question_payload.get("field") == "subject"
    )
    old_key = subject_clar.clarification_key
    # Expand the Mercury candidate set by giving a second entity the same
    # canonical name (create under a unique name first to bypass identity gate).
    other = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=f"Mercury-alt-{uuid.uuid4().hex[:6]}",
            class_key="Organization",
        )
    )
    assert other.entity is not None
    other_row = db_session.get(Entity, other.entity.id)
    assert other_row is not None
    other_row.canonical_name = "Mercury"
    db_session.flush()
    KnowledgeIngestionService(db_session).update_candidate_state(
        cid, KnowledgeCandidateState.ACTIONABLE, blockers_json=[]
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    second = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject2 = next(
        c for c in second.open_clarifications if c.question_payload.get("field") == "subject"
    )
    assert subject2.clarification_key != old_key
    prior = KnowledgeIngestionService(db_session).get_clarification(subject_clar.clarification_id)
    assert prior.status == KnowledgeIngestionClarificationStatus.SUPERSEDED.value
    assert subject2.supersedes_clarification_id == subject_clar.clarification_id


def test_high_impact_root_ranks_before_low_impact(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _name_only_entity(db_session, actor_key=actor_key, name="Venus")
    _name_only_entity(db_session, actor_key=actor_key, name="Mars")
    _name_only_entity(db_session, actor_key=actor_key, name="Jupiter")
    root = _add_candidate(
        db_session,
        ingestion_id,
        key="root",
        claim_text="Mercury relatedTo Venus as a root ambiguity",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Venus"},
        },
    )
    child = _add_candidate(
        db_session,
        ingestion_id,
        key="child",
        claim_text="Mars relatedTo Jupiter as a leaf ambiguity",
        claim_payload={
            "subject": {"text": "Mars"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Jupiter"},
        },
    )
    KnowledgeIngestionService(db_session).add_dependency(
        parent_candidate_id=root,
        child_candidate_id=child,
        dependency_kind=KnowledgeCandidateDependencyKind.REQUIRES_RESOLUTION,
    )
    result = _resolve_and_plan(db_session, ingestion_id)
    identity = [c for c in result.open_clarifications if c.clarification_kind == "identity"]
    assert identity
    # Root-linked clarifications should have impact >= leaf-only ones.
    by_root = {c.root_candidate_id: c.impact_blocked_count for c in identity}
    if root in by_root and child in by_root:
        assert by_root[root] >= by_root[child]
    assert identity[0].impact_blocked_count >= identity[-1].impact_blocked_count


def test_chosen_entity_answer_resolves_and_no_wcr(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    mid = _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _name_only_entity(db_session, actor_key=actor_key, name="Venus")
    vid = _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    # Venus side: use authoritative alias so only subject blocks.
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="choose",
        claim_text="Mercury relatedTo VenusAuth after clarification",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    del vid
    before = _counts(db_session)
    wcr_before = int(
        db_session.scalar(select(func.count()).select_from(WriteClarificationRequest)) or 0
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(
        c for c in planned.open_clarifications if c.question_payload.get("field") == "subject"
    )
    cont = KnowledgeContinuationService(db_session)
    result = cont.continue_ingestion(
        ingestion_id,
        answers=[
            ClarificationAnswerInput(
                clarification_id=subject.clarification_id,
                answer_payload={
                    "resolution": IdentityAnswerResolution.CHOSEN_ENTITY.value,
                    "chosen_entity_id": str(mid),
                },
            )
        ],
    )
    after = _counts(db_session)
    wcr_after = int(
        db_session.scalar(select(func.count()).select_from(WriteClarificationRequest)) or 0
    )
    assert after == before
    assert wcr_after == wcr_before
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["subject"]["disposition"] == ProjectionDisposition.REUSE.value
    assert row.resolution_json["subject"]["entity_id"] == str(mid)
    assert result.ready_for_commit is True


def test_chosen_entity_not_in_set_rejected(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _name_only_entity(db_session, actor_key=actor_key, name="Venus")
    foreign = _auth_alias(db_session, actor_key=actor_key, name="OtherOrg")
    _add_candidate(
        db_session,
        ingestion_id,
        key="bad-choice",
        claim_text="Mercury relatedTo Venus rejects foreign choice",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Venus"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    clar = planned.open_clarifications[0]
    try:
        KnowledgeContinuationService(db_session).continue_ingestion(
            ingestion_id,
            answers=[
                ClarificationAnswerInput(
                    clarification_id=clar.clarification_id,
                    answer_payload={
                        "resolution": "chosen_entity",
                        "chosen_entity_id": str(foreign),
                    },
                )
            ],
        )
        raise AssertionError("expected ValidationFailedError")
    except ValidationFailedError as exc:
        assert "not in the offered" in str(exc)


def test_create_new_future_create_no_entity(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="create-new",
        claim_text="Mercury relatedTo VenusAuth via create_new choice",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    before = _counts(db_session)
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(
        c for c in planned.open_clarifications if c.question_payload.get("field") == "subject"
    )
    KnowledgeContinuationService(db_session).continue_ingestion(
        ingestion_id,
        answers=[
            ClarificationAnswerInput(
                clarification_id=subject.clarification_id,
                answer_payload={"resolution": "create_new"},
            )
        ],
    )
    assert _counts(db_session) == before
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert row.resolution_json["subject"]["disposition"] == ProjectionDisposition.CREATE.value


def test_reject_discards_candidate(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="reject",
        claim_text="Mercury relatedTo VenusAuth rejected by user",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(
        c for c in planned.open_clarifications if c.question_payload.get("field") == "subject"
    )
    result = KnowledgeContinuationService(db_session).continue_ingestion(
        ingestion_id,
        answers=[
            ClarificationAnswerInput(
                clarification_id=subject.clarification_id,
                answer_payload={"resolution": "reject"},
            )
        ],
    )
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.DISCARDED.value
    assert result.discarded_count == 1


def test_stale_answered_choice_issues_fresh_clarification(db_session: Session) -> None:
    """Historical answers stay answered; changed ambiguity opens a new row."""
    actor_key, ingestion_id = _ingestion(db_session)
    mid = _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="stale",
        claim_text="Mercury relatedTo VenusAuth until chosen entity disappears",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(
        c for c in planned.open_clarifications if c.question_payload.get("field") == "subject"
    )
    KnowledgeContinuationService(db_session).continue_ingestion(
        ingestion_id,
        answers=[
            ClarificationAnswerInput(
                clarification_id=subject.clarification_id,
                answer_payload={
                    "resolution": "chosen_entity",
                    "chosen_entity_id": str(mid),
                },
            )
        ],
    )
    answered = KnowledgeIngestionService(db_session).get_clarification(subject.clarification_id)
    assert answered.status == KnowledgeIngestionClarificationStatus.ANSWERED.value

    # Deactivate the chosen entity and introduce a new Mercury candidate so
    # revalidation cannot silently reuse the historical answer.
    entity = db_session.get(Entity, mid)
    assert entity is not None
    entity.status = EntityStatus.DEPRECATED.value
    db_session.flush()
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")

    KnowledgeIngestionService(db_session).update_candidate_state(
        cid, KnowledgeCandidateState.ACTIONABLE, blockers_json=[]
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    replanned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    still_answered = KnowledgeIngestionService(db_session).get_clarification(
        subject.clarification_id
    )
    assert still_answered.status == KnowledgeIngestionClarificationStatus.ANSWERED.value
    assert still_answered.answer_payload == answered.answer_payload
    fresh = [c for c in replanned.open_clarifications if c.clarification_kind == "identity"]
    assert fresh
    assert all(c.clarification_id != subject.clarification_id for c in fresh)
    # Answered prior stays answered; new row still links lineage.
    assert any(c.supersedes_clarification_id == subject.clarification_id for c in fresh)


def test_same_entity_ids_status_change_creates_new_clarification_key(db_session: Session) -> None:
    """Fingerprint includes status/class_keys so stale answers cannot occupy the unique key."""
    from semantic_memory.services.knowledge_resolution.schemas import (
        BlockerType,
        CandidateResolution,
        CommitPath,
        EntityBindPlan,
        ProjectionDisposition,
        ResolutionBlocker,
    )

    actor_key, ingestion_id = _ingestion(db_session)
    a = _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    other = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=f"Mercury-twin-{uuid.uuid4().hex[:6]}",
            class_key="Organization",
        )
    )
    assert other.entity is not None
    b_row = db_session.get(Entity, other.entity.id)
    assert b_row is not None
    b_row.canonical_name = "Mercury"
    db_session.flush()
    b = other.entity.id
    _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="same-ids",
        claim_text="Mercury relatedTo VenusAuth with two Mercury candidates",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(
        c for c in planned.open_clarifications if c.question_payload.get("field") == "subject"
    )
    old_key = subject.clarification_key
    KnowledgeContinuationService(db_session).continue_ingestion(
        ingestion_id,
        answers=[
            ClarificationAnswerInput(
                clarification_id=subject.clarification_id,
                answer_payload={
                    "resolution": "chosen_entity",
                    "chosen_entity_id": str(a),
                },
            )
        ],
    )
    answered = KnowledgeIngestionService(db_session).get_clarification(subject.clarification_id)
    assert answered.status == KnowledgeIngestionClarificationStatus.ANSWERED.value

    # Same offered IDs, but chosen entity status materially changes.
    a_entity = db_session.get(Entity, a)
    assert a_entity is not None
    a_entity.status = EntityStatus.DEPRECATED.value
    db_session.flush()

    ki = KnowledgeIngestionService(db_session)
    ki.persist_candidate_resolution(
        cid,
        state=KnowledgeCandidateState.BLOCKED,
        resolution=CandidateResolution(
            commit_path=CommitPath.DOMAIN_ASSERTION,
            subject=EntityBindPlan(
                disposition=ProjectionDisposition.CLARIFY,
                text="Mercury",
                candidate_entity_ids=[a, b],
                detail="forced_same_ids_stale_status",
            ),
            claim_text="Mercury relatedTo VenusAuth with two Mercury candidates",
        ),
        blockers=[
            ResolutionBlocker(
                type=BlockerType.IDENTITY_CLARIFICATION,
                field="subject",
                ref="Mercury",
                detail="required_identity_ambiguous",
                required=True,
            )
        ],
    )
    replanned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    fresh = next(
        c for c in replanned.open_clarifications if c.question_payload.get("field") == "subject"
    )
    assert fresh.clarification_key != old_key
    assert fresh.clarification_id != subject.clarification_id
    assert fresh.supersedes_clarification_id == subject.clarification_id
    still = ki.get_clarification(subject.clarification_id)
    assert still.status == KnowledgeIngestionClarificationStatus.ANSWERED.value
    # Unique constraint: both rows coexist under different keys.
    assert ki.find_clarification_by_key(ingestion_id, old_key) is not None
    assert ki.find_clarification_by_key(ingestion_id, fresh.clarification_key) is not None


def test_answer_replay_idempotent_and_different_rejected(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    mid = _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    _add_candidate(
        db_session,
        ingestion_id,
        key="idem",
        claim_text="Mercury relatedTo VenusAuth for answer idempotency",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(
        c for c in planned.open_clarifications if c.question_payload.get("field") == "subject"
    )
    payload = {"resolution": "chosen_entity", "chosen_entity_id": str(mid)}
    ki = KnowledgeIngestionService(db_session)
    first = ki.answer_clarification(subject.clarification_id, answer_payload=payload)
    second = ki.answer_clarification(subject.clarification_id, answer_payload=payload)
    assert first.id == second.id
    try:
        ki.answer_clarification(
            subject.clarification_id,
            answer_payload={"resolution": "create_new"},
        )
        raise AssertionError("expected InvalidStateTransitionError")
    except InvalidStateTransitionError:
        pass


def test_awaiting_without_answers_no_resolver_churn(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _name_only_entity(db_session, actor_key=actor_key, name="Venus")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="await",
        claim_text="Mercury relatedTo Venus waiting for answers",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "Venus"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    cont = KnowledgeContinuationService(db_session)
    planned = cont.plan_clarifications(ingestion_id)
    KnowledgeIngestionService(db_session).set_ingestion_status(
        ingestion_id, KnowledgeIngestionStatus.AWAITING_CLARIFICATION
    )
    attempts_before = KnowledgeIngestionService(db_session).get_candidate(cid).attempt_count
    again = cont.continue_ingestion(ingestion_id, answers=[])
    attempts_after = KnowledgeIngestionService(db_session).get_candidate(cid).attempt_count
    assert attempts_after == attempts_before
    assert again.open_clarifications
    assert again.status == KnowledgeIngestionStatus.AWAITING_CLARIFICATION.value
    assert len(again.open_clarifications) == len(planned.open_clarifications)


def test_paused_resume_fresh_slice_cumulative_attempts(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session, budgets={"max_candidate_attempts": 1})
    _add_candidate(
        db_session,
        ingestion_id,
        key="q1",
        kind=KnowledgeCandidateKind.QUESTION,
        claim_text="Question one for budget pause?",
    )
    _add_candidate(
        db_session,
        ingestion_id,
        key="q2",
        kind=KnowledgeCandidateKind.QUESTION,
        claim_text="Question two for budget pause?",
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    ingestion = KnowledgeIngestionService(db_session).get_ingestion(ingestion_id)
    assert ingestion.status == KnowledgeIngestionStatus.PAUSED.value
    assert ingestion.pause_reason == KnowledgeIngestionPauseReason.BUDGET_EXHAUSTED.value
    attempts = {
        c.candidate_key: c.attempt_count
        for c in KnowledgeIngestionService(db_session).list_candidates(ingestion_id)
    }
    result = KnowledgeContinuationService(db_session).continue_ingestion(ingestion_id)
    attempts2 = {
        c.candidate_key: c.attempt_count
        for c in KnowledgeIngestionService(db_session).list_candidates(ingestion_id)
    }
    assert sum(attempts2.values()) >= sum(attempts.values())
    assert result.cumulative_stats.get("candidates_attempted", 0) >= 1


def test_ready_to_apply_is_external_not_clarification(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _auth_alias(db_session, actor_key=actor_key, name="ECorp")
    _auth_alias(db_session, actor_key=actor_key, name="FCorp")
    _add_candidate(
        db_session,
        ingestion_id,
        key="gap",
        claim_text="ECorp competitivelyDifferentiates FCorp for external apply",
        claim_payload={
            "subject": {"text": "ECorp"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "FCorp"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    result = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    assert all(c.clarification_kind != "ontology" for c in result.open_clarifications) or all(
        c.ontology_clarification_request_id is not None
        for c in result.open_clarifications
        if c.clarification_kind == "ontology"
    )
    # READY_TO_APPLY / MANUAL_REVIEW without clarification id → external apply blockers.
    has_apply = any(b.kind.value == "ontology_apply" for b in result.unresolved_external_blockers)
    has_ont_clar = any(c.clarification_kind == "ontology" for c in result.open_clarifications)
    assert has_apply or has_ont_clar


def test_external_apply_plus_continue_clears_blocker(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    _auth_alias(db_session, actor_key=actor_key, name="KCorp")
    _auth_alias(db_session, actor_key=actor_key, name="LCorp")
    cid = _add_candidate(
        db_session,
        ingestion_id,
        key="ext",
        claim_text="KCorp competitivelyDifferentiates LCorp until applied",
        claim_payload={
            "subject": {"text": "KCorp"},
            "predicate_key_hint": "competitivelyDifferentiates",
            "object": {"text": "LCorp"},
        },
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    row = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert row.state == KnowledgeCandidateState.BLOCKED.value
    proposal_id = uuid.UUID(row.resolution_json["ontology_proposal_ids"][0])
    ProposalService(db_session).apply_proposal(
        ApplyProposalRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            proposal_id=proposal_id,
        )
    )
    result = KnowledgeContinuationService(db_session).continue_ingestion(ingestion_id)
    again = KnowledgeIngestionService(db_session).get_candidate(cid)
    assert again.state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert again.blockers_json == []
    assert result.ready_for_commit is True


def test_dependency_cycle_is_non_answerable_blocker(db_session: Session) -> None:
    _, ingestion_id = _ingestion(db_session)
    a = _add_candidate(
        db_session,
        ingestion_id,
        key="cycle-a",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Cycle A",
    )
    b = _add_candidate(
        db_session,
        ingestion_id,
        key="cycle-b",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Cycle B",
    )
    ki = KnowledgeIngestionService(db_session)
    ki.add_dependency(parent_candidate_id=a, child_candidate_id=b)
    ki.add_dependency(parent_candidate_id=b, child_candidate_id=a)
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    result = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    assert result.open_clarifications == []
    assert any(b.kind.value == "dependency_stall" for b in result.unresolved_external_blockers)
    assert result.status == KnowledgeIngestionStatus.RESOLVING.value or result.blocked_count >= 2


def test_fully_resolved_execute_ready_for_commit_no_writes(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    actor_key, actor_id, source_id = _actor_source(db_session)
    rev = ProvenanceRepository(db_session).create_content_revision(
        source_id=source_id,
        revision_number=1,
        canonical_content=content,
        canonical_format="text",
        canonical_content_hash=hashlib.sha256(content.encode()).hexdigest(),
        captured_at=datetime.now(UTC),
        created_by_actor_id=actor_id,
    )
    ki = KnowledgeIngestionService(db_session)
    ing = ki.create_ingestion(
        actor_id=actor_id,
        source_id=source_id,
        source_hash=rev.canonical_content_hash,
        request_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        pipeline_version="ki-cont-v1",
        ontology_revision_marker="core@test",
        mode=KnowledgeIngestionMode.EXECUTE,
        source_content_revision_id=rev.id,
    )
    KnowledgeExtractionService(db_session).extract(ing.id)
    before = _counts(db_session)
    result = KnowledgeContinuationService(db_session).continue_ingestion(ing.id)
    after = _counts(db_session)
    assert after == before
    assert result.open_clarifications == []
    assert result.ready_for_commit is True
    assert result.status != KnowledgeIngestionStatus.COMMITTING.value
    assert ki.list_effects(ing.id) == []
    del actor_key


def test_dry_run_would_complete_never_committing(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    actor_key, actor_id, source_id = _actor_source(db_session)
    rev = ProvenanceRepository(db_session).create_content_revision(
        source_id=source_id,
        revision_number=1,
        canonical_content=content,
        canonical_format="text",
        canonical_content_hash=hashlib.sha256(content.encode()).hexdigest(),
        captured_at=datetime.now(UTC),
        created_by_actor_id=actor_id,
    )
    ki = KnowledgeIngestionService(db_session)
    ing = ki.create_ingestion(
        actor_id=actor_id,
        source_id=source_id,
        source_hash=rev.canonical_content_hash,
        request_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        pipeline_version="ki-cont-v1",
        ontology_revision_marker="core@test",
        mode=KnowledgeIngestionMode.DRY_RUN,
        source_content_revision_id=rev.id,
    )
    KnowledgeExtractionService(db_session).extract(ing.id)
    result = KnowledgeContinuationService(db_session).continue_ingestion(ing.id)
    assert result.ready_for_commit is False
    assert result.would_complete is True
    assert result.status != KnowledgeIngestionStatus.COMMITTING.value
    del actor_key


def test_answering_root_wakes_dependent_child(db_session: Session) -> None:
    actor_key, ingestion_id = _ingestion(db_session)
    mid = _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    parent = _add_candidate(
        db_session,
        ingestion_id,
        key="parent",
        claim_text="Mercury relatedTo VenusAuth as parent fact",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    child = _add_candidate(
        db_session,
        ingestion_id,
        key="child",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Child waits on parent resolution",
    )
    KnowledgeIngestionService(db_session).add_dependency(
        parent_candidate_id=parent, child_candidate_id=child
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(c for c in planned.open_clarifications if c.root_candidate_id == parent)
    KnowledgeContinuationService(db_session).continue_ingestion(
        ingestion_id,
        answers=[
            ClarificationAnswerInput(
                clarification_id=subject.clarification_id,
                answer_payload={
                    "resolution": "chosen_entity",
                    "chosen_entity_id": str(mid),
                },
            )
        ],
    )
    ki = KnowledgeIngestionService(db_session)
    assert ki.get_candidate(parent).state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value
    assert ki.get_candidate(child).state == KnowledgeCandidateState.RESOLVED_COMMIT_ELIGIBLE.value


def test_reject_discards_parent_children_reevaluated_not_auto_satisfied(
    db_session: Session,
) -> None:
    """Discarded parent wakes dependents but does not satisfy V1 parent-ready rule."""
    from semantic_memory.services.knowledge_resolution.schemas import BlockerType

    actor_key, ingestion_id = _ingestion(db_session)
    _name_only_entity(db_session, actor_key=actor_key, name="Mercury")
    _auth_alias(db_session, actor_key=actor_key, name="VenusAuth")
    parent = _add_candidate(
        db_session,
        ingestion_id,
        key="reject-parent",
        claim_text="Mercury relatedTo VenusAuth rejected parent",
        claim_payload={
            "subject": {"text": "Mercury"},
            "predicate_key_hint": "relatedTo",
            "object": {"text": "VenusAuth"},
        },
    )
    child = _add_candidate(
        db_session,
        ingestion_id,
        key="reject-child",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        claim_text="Child depends on rejected parent resolution",
    )
    KnowledgeIngestionService(db_session).add_dependency(
        parent_candidate_id=parent,
        child_candidate_id=child,
        dependency_kind=KnowledgeCandidateDependencyKind.REQUIRES_RESOLUTION,
    )
    KnowledgeResolutionService(db_session).resolve_ingestion(ingestion_id)
    planned = KnowledgeContinuationService(db_session).plan_clarifications(ingestion_id)
    subject = next(c for c in planned.open_clarifications if c.root_candidate_id == parent)
    KnowledgeContinuationService(db_session).continue_ingestion(
        ingestion_id,
        answers=[
            ClarificationAnswerInput(
                clarification_id=subject.clarification_id,
                answer_payload={"resolution": "reject"},
            )
        ],
    )
    ki = KnowledgeIngestionService(db_session)
    assert ki.get_candidate(parent).state == KnowledgeCandidateState.DISCARDED.value
    child_row = ki.get_candidate(child)
    # Re-evaluated (not left EXTRACTED forever) but dependency not auto-satisfied.
    assert child_row.state == KnowledgeCandidateState.BLOCKED.value
    assert any(b.get("type") == BlockerType.DEPENDENCY.value for b in child_row.blockers_json)


def test_ontology_package_reconciles_when_linked_handle_already_answered(
    db_session: Session,
) -> None:
    """Crash/retry: ontology answered, package still open → continue recovers."""
    from semantic_memory.models.enums import SemanticClarificationStatus
    from semantic_memory.models.governance import OntologySemanticClarificationRequest
    from semantic_memory.schemas.proposals import ProposeClassRequest
    from semantic_memory.schemas.semantic_review import (
        CLARIFICATION_REASON_CODE,
        AnswerSemanticClarificationRequest,
    )
    from semantic_memory.services.review import MockSemanticReviewer, ReviewDecision

    actor_key, ingestion_id = _ingestion(db_session)
    proposals = ProposalService(db_session, reviewer=MockSemanticReviewer())
    proposed = proposals.propose_class(
        ProposeClassRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            key=f"CapabilityArea{uuid.uuid4().hex[:6]}",
            description="Some kind of ability or focus area related to agents.",
            parent_keys=["Thing"],
            metadata={
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
            },
        )
    )
    assert proposed.open_clarification_request is not None
    ont_id = proposed.open_clarification_request.clarification_request_id
    assert ont_id is not None

    # Answer ontology handle successfully (first durable write of the split pair).
    proposals.answer_semantic_clarification(
        AnswerSemanticClarificationRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            clarification_request_id=ont_id,
            response="CapabilityArea is distinct from Skill; keep as its own class.",
        )
    )
    linked = db_session.get(OntologySemanticClarificationRequest, ont_id)
    assert linked is not None
    assert linked.status in {
        SemanticClarificationStatus.ANSWERED.value,
        SemanticClarificationStatus.RESOLVED.value,
    }
    terminal_status = linked.status

    # Package clarification left open (simulates crash before package mark-answered).
    pkg = KnowledgeIngestionService(db_session).record_clarification(
        ingestion_id=ingestion_id,
        clarification_key=f"ontology:clarification:{ont_id}",
        clarification_kind=KnowledgeIngestionClarificationKind.ONTOLOGY,
        question_payload={
            "ontology_clarification_request_id": str(ont_id),
            "detail": "split_brain_test",
        },
        ontology_clarification_request_id=ont_id,
        impact_blocked_count=1,
    )
    assert pkg.status == KnowledgeIngestionClarificationStatus.OPEN.value
    KnowledgeIngestionService(db_session).set_ingestion_status(
        ingestion_id, KnowledgeIngestionStatus.AWAITING_CLARIFICATION
    )

    # continue without answers must reconcile rather than stuck / re-answer.
    result = KnowledgeContinuationService(db_session).continue_ingestion(ingestion_id, answers=[])
    advanced = KnowledgeIngestionService(db_session).get_clarification(pkg.id)
    assert advanced.status == KnowledgeIngestionClarificationStatus.ANSWERED.value
    assert advanced.answer_payload.get("reconciled") is True
    assert advanced.answer_payload.get("ontology_status") == terminal_status
    # Linked ontology handle remains one-shot terminal (not re-opened / re-answered).
    linked_after = db_session.get(OntologySemanticClarificationRequest, ont_id)
    assert linked_after is not None
    assert linked_after.status == terminal_status
    assert result.status != KnowledgeIngestionStatus.FAILED.value
