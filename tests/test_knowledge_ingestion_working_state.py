"""Phase B: durable knowledge-ingestion working-state invariants."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    IdempotencyKeyReusedError,
    InvalidStateTransitionError,
    ValidationFailedError,
)
from semantic_memory.models import ActorType, Source
from semantic_memory.models.enums import (
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    KnowledgeCandidateState,
    KnowledgeIngestionClarificationKind,
    KnowledgeIngestionEffectStatus,
    KnowledgeIngestionEffectType,
    KnowledgeIngestionMode,
    KnowledgeIngestionPauseReason,
    KnowledgeIngestionStatus,
)
from semantic_memory.models.knowledge_ingestion import KnowledgeIngestion
from semantic_memory.repositories.provenance import ProvenanceRepository
from semantic_memory.repositories.write_clarifications import WriteClarificationRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService


def _actor_and_source(session: Session, suffix: str | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    key = f"ki-writer-{suffix or uuid.uuid4().hex[:8]}"
    actor = ActorService(session).ensure(ActorEnsureRequest(key=key, actor_type=ActorType.AGENT))
    source = Source(
        id=uuid.uuid4(),
        source_system="test",
        external_id=f"pkg-{uuid.uuid4().hex[:8]}",
        metadata_json={},
        created_by_actor_id=actor.id,
    )
    session.add(source)
    session.flush()
    return actor.id, source.id


def _make_source_revision(
    session: Session, *, actor_id: uuid.UUID, source_id: uuid.UUID, body: str = "pkg body"
) -> uuid.UUID:
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    rev = ProvenanceRepository(session).create_content_revision(
        source_id=source_id,
        revision_number=ProvenanceRepository(session).next_revision_number(source_id),
        canonical_content=body,
        canonical_format="markdown",
        canonical_content_hash=content_hash,
        captured_at=datetime.now(UTC),
        created_by_actor_id=actor_id,
    )
    return rev.id


def _create_ingestion(
    session: Session,
    *,
    mode: KnowledgeIngestionMode = KnowledgeIngestionMode.EXECUTE,
    idempotency_key: str | None = None,
    actor_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    fix_revision: bool = True,
) -> KnowledgeIngestion:
    if actor_id is None or source_id is None:
        actor_id, source_id = _actor_and_source(session)
    service = KnowledgeIngestionService(session)
    ingestion = service.create_ingestion(
        actor_id=actor_id,
        source_id=source_id,
        source_hash="sha256:deadbeef",
        request_id=uuid.uuid4(),
        idempotency_key=idempotency_key or str(uuid.uuid4()),
        pipeline_version="ki-v1",
        ontology_revision_marker="core@test",
        mode=mode,
    )
    if fix_revision:
        revision_id = _make_source_revision(session, actor_id=actor_id, source_id=source_id)
        return service.attach_source_content_revision(ingestion.id, revision_id)
    return ingestion


def _candidate(
    service: KnowledgeIngestionService,
    ingestion_id: uuid.UUID,
    key: str,
    *,
    kind: KnowledgeCandidateKind = KnowledgeCandidateKind.HYPOTHESIS,
    epistemic: KnowledgeCandidateEpistemicStatus = KnowledgeCandidateEpistemicStatus.ACTIVE,
    state: KnowledgeCandidateState = KnowledgeCandidateState.EXTRACTED,
) -> uuid.UUID:
    row = service.create_candidate(
        ingestion_id=ingestion_id,
        candidate_key=key,
        kind=kind,
        polarity=KnowledgeCandidatePolarity.POSITIVE,
        epistemic_status=epistemic,
        derivation=KnowledgeCandidateDerivation.EXPLICIT,
        claim_text="maybe engines fail",
        claim_payload={"subject": "engine"},
        source_span={"path": "/findings/0"},
        source_context_path=["findings", "0"],
        state=state,
    )
    return row.id


def test_ingestion_idempotency_unique_per_actor(db_session: Session) -> None:
    actor_id, source_id = _actor_and_source(db_session)
    service = KnowledgeIngestionService(db_session)
    key = "idem-ki-1"
    first = service.create_ingestion(
        actor_id=actor_id,
        source_id=source_id,
        source_hash="h1",
        request_id=uuid.uuid4(),
        idempotency_key=key,
        pipeline_version="ki-v1",
        ontology_revision_marker="m1",
    )
    found = service.find_by_idempotency(actor_id, key)
    assert found is not None
    assert found.id == first.id

    with pytest.raises(IdempotencyKeyReusedError):
        service.create_ingestion(
            actor_id=actor_id,
            source_id=source_id,
            source_hash="h2",
            request_id=uuid.uuid4(),
            idempotency_key=key,
            pipeline_version="ki-v1",
            ontology_revision_marker="m1",
        )

    # Same key is allowed for a different actor.
    other_actor, other_source = _actor_and_source(db_session, "other")
    second = service.create_ingestion(
        actor_id=other_actor,
        source_id=other_source,
        source_hash="h3",
        request_id=uuid.uuid4(),
        idempotency_key=key,
        pipeline_version="ki-v1",
        ontology_revision_marker="m1",
    )
    assert second.id != first.id


def test_candidate_key_unique_only_within_ingestion(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    a = _create_ingestion(db_session)
    b = _create_ingestion(db_session)
    _candidate(service, a.id, "c1")
    _candidate(service, b.id, "c1")  # same key, different ingestion — ok

    with pytest.raises(ValidationFailedError, match="unique within an ingestion"):
        _candidate(service, a.id, "c1")


def test_candidate_epistemic_round_trip(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    cid = _candidate(
        service,
        ingestion.id,
        "hyp-1",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        epistemic=KnowledgeCandidateEpistemicStatus.ACTIVE,
    )
    loaded = service.get_candidate(cid)
    assert loaded.kind == KnowledgeCandidateKind.HYPOTHESIS.value
    assert loaded.epistemic_status == KnowledgeCandidateEpistemicStatus.ACTIVE.value
    assert loaded.polarity == KnowledgeCandidatePolarity.POSITIVE.value
    assert loaded.derivation == KnowledgeCandidateDerivation.EXPLICIT.value
    assert loaded.claim_text == "maybe engines fail"
    assert loaded.claim_payload == {"subject": "engine"}
    assert loaded.source_context_path == ["findings", "0"]


def test_rejected_hypothesis_is_epistemic_not_processing_state(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    cid = _candidate(
        service,
        ingestion.id,
        "hyp-rej",
        kind=KnowledgeCandidateKind.HYPOTHESIS,
        epistemic=KnowledgeCandidateEpistemicStatus.ACTIVE,
        state=KnowledgeCandidateState.ACTIONABLE,
    )
    service.update_candidate_epistemic_status(cid, KnowledgeCandidateEpistemicStatus.REJECTED)
    loaded = service.get_candidate(cid)
    assert loaded.epistemic_status == KnowledgeCandidateEpistemicStatus.REJECTED.value
    assert loaded.state == KnowledgeCandidateState.ACTIONABLE.value
    assert loaded.state != loaded.epistemic_status


def test_dependency_duplicates_rejected(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    parent = _candidate(service, ingestion.id, "p")
    child = _candidate(service, ingestion.id, "c")
    service.add_dependency(parent_candidate_id=parent, child_candidate_id=child)
    with pytest.raises(ValidationFailedError, match="Duplicate"):
        service.add_dependency(parent_candidate_id=parent, child_candidate_id=child)


def test_self_dependency_rejected(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    cid = _candidate(service, ingestion.id, "solo")
    with pytest.raises(ValidationFailedError, match="Self-dependency"):
        service.add_dependency(parent_candidate_id=cid, child_candidate_id=cid)


def test_cross_ingestion_dependency_rejected(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    a = _create_ingestion(db_session)
    b = _create_ingestion(db_session)
    parent = _candidate(service, a.id, "p")
    child = _candidate(service, b.id, "c")
    with pytest.raises(ValidationFailedError, match="same ingestion"):
        service.add_dependency(parent_candidate_id=parent, child_candidate_id=child)


def test_clarification_links_write_clarification_handle(db_session: Session) -> None:
    actor_id, source_id = _actor_and_source(db_session)
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session, actor_id=actor_id, source_id=source_id)
    handle = WriteClarificationRepository(db_session).create(
        actor_id=actor_id,
        operation_name="assert_statement",
        operation_mode="execute",
        original_request_id=uuid.uuid4(),
        ambiguous_path="subject",
        frozen_request={"predicate_key": "name"},
        identity_snapshot={"candidates": []},
        candidate_entity_ids=[],
        ttl_minutes=30,
    )
    clarification = service.record_clarification(
        ingestion_id=ingestion.id,
        clarification_kind=KnowledgeIngestionClarificationKind.IDENTITY,
        question_payload={"q": "which engine?"},
        write_clarification_request_id=handle.id,
        impact_blocked_count=2,
    )
    assert clarification.write_clarification_request_id == handle.id
    assert clarification.status == "open"
    answered = service.answer_clarification(
        clarification.id, answer_payload={"resolution": "create_new"}
    )
    assert answered.status == "answered"
    assert answered.answer_payload == {"resolution": "create_new"}
    assert answered.answered_at is not None


def test_effect_key_unique_and_multiple_assert_statement(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    cid = _candidate(service, ingestion.id, "claim-1")
    e1 = service.record_effect(
        ingestion_id=ingestion.id,
        candidate_id=cid,
        effect_key="statement:makesClaim",
        effect_type=KnowledgeIngestionEffectType.ASSERT_STATEMENT,
        status=KnowledgeIngestionEffectStatus.APPLIED,
    )
    e2 = service.record_effect(
        ingestion_id=ingestion.id,
        candidate_id=cid,
        effect_key="statement:claimText",
        effect_type=KnowledgeIngestionEffectType.ASSERT_STATEMENT,
        status=KnowledgeIngestionEffectStatus.APPLIED,
    )
    assert e1.id != e2.id
    assert e1.effect_type == e2.effect_type == KnowledgeIngestionEffectType.ASSERT_STATEMENT.value

    with pytest.raises(ValidationFailedError, match="effect_key must be unique"):
        service.record_effect(
            ingestion_id=ingestion.id,
            candidate_id=cid,
            effect_key="statement:makesClaim",
            effect_type=KnowledgeIngestionEffectType.ASSERT_STATEMENT,
        )


def test_failed_effect_becomes_applied_same_row(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    cid = _candidate(service, ingestion.id, "ev-1")
    service.record_effect(
        ingestion_id=ingestion.id,
        candidate_id=cid,
        effect_key="evidence:makesClaim",
        effect_type=KnowledgeIngestionEffectType.ADD_EVIDENCE,
        status=KnowledgeIngestionEffectStatus.FAILED,
        error_code="TRANSIENT",
        error_message="timeout",
    )
    applied = service.mark_effect_applied(
        ingestion_id=ingestion.id,
        candidate_id=cid,
        effect_key="evidence:makesClaim",
        details_json={"retried": True},
    )
    again = service.get_effect_by_key(ingestion.id, cid, "evidence:makesClaim")
    assert again is not None
    assert again.id == applied.id
    assert again.status == KnowledgeIngestionEffectStatus.APPLIED.value
    assert again.error_code is None
    assert again.error_message is None
    assert len(service.list_effects(ingestion.id)) == 1


def test_dry_run_mode_distinct_and_never_committing(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    dry = _create_ingestion(db_session, mode=KnowledgeIngestionMode.DRY_RUN)
    exe = _create_ingestion(db_session, mode=KnowledgeIngestionMode.EXECUTE)
    assert dry.mode == KnowledgeIngestionMode.DRY_RUN.value
    assert exe.mode == KnowledgeIngestionMode.EXECUTE.value
    assert dry.status == KnowledgeIngestionStatus.ACCEPTED.value

    with pytest.raises(InvalidStateTransitionError, match="Dry-run"):
        service.set_ingestion_status(dry.id, KnowledgeIngestionStatus.COMMITTING)

    # DB check constraint also rejects dry_run + committing.
    with db_session.begin_nested():
        dry_row = db_session.get(KnowledgeIngestion, dry.id)
        assert dry_row is not None
        dry_row.status = KnowledgeIngestionStatus.COMMITTING.value
        with pytest.raises(IntegrityError):
            db_session.flush()


def test_paused_budget_exhausted_round_trip(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    paused = service.mark_paused_budget_exhausted(ingestion.id, stats_json={"attempts": 3})
    assert paused.status == KnowledgeIngestionStatus.PAUSED.value
    assert paused.pause_reason == KnowledgeIngestionPauseReason.BUDGET_EXHAUSTED.value
    assert paused.stats_json == {"attempts": 3}
    reloaded = service.get_ingestion(ingestion.id)
    assert reloaded.status == KnowledgeIngestionStatus.PAUSED.value
    assert reloaded.pause_reason == KnowledgeIngestionPauseReason.BUDGET_EXHAUSTED.value


def test_completed_ingestion_retains_candidates_and_effects(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    cid = _candidate(service, ingestion.id, "keep-me")
    service.record_effect(
        ingestion_id=ingestion.id,
        candidate_id=cid,
        effect_key="claim:create",
        effect_type=KnowledgeIngestionEffectType.CREATE_ENTITY,
        status=KnowledgeIngestionEffectStatus.APPLIED,
    )
    service.record_clarification(
        ingestion_id=ingestion.id,
        clarification_kind=KnowledgeIngestionClarificationKind.PACKAGE_LOCAL,
        question_payload={"note": "audit"},
    )
    completed = service.mark_completed(ingestion.id, stats_json={"candidates": 1})
    assert completed.status == KnowledgeIngestionStatus.COMPLETED.value
    assert completed.completed_at is not None

    assert len(service.list_candidates(ingestion.id)) == 1
    assert len(service.list_effects(ingestion.id)) == 1
    assert len(service.list_clarifications(ingestion.id)) == 1


def test_dry_run_cannot_record_persistence_effects(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    dry = _create_ingestion(db_session, mode=KnowledgeIngestionMode.DRY_RUN)
    cid = _candidate(service, dry.id, "preview-only")
    for effect_type, effect_key in (
        (KnowledgeIngestionEffectType.CREATE_ENTITY, "claim:create"),
        (KnowledgeIngestionEffectType.ASSERT_STATEMENT, "statement:makesClaim"),
        (KnowledgeIngestionEffectType.ADD_EVIDENCE, "evidence:makesClaim"),
    ):
        with pytest.raises(InvalidStateTransitionError, match="never record persistence effects"):
            service.record_effect(
                ingestion_id=dry.id,
                candidate_id=cid,
                effect_key=effect_key,
                effect_type=effect_type,
            )
    assert service.list_effects(dry.id) == []


def test_candidate_requires_fixed_source_revision(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session, fix_revision=False)
    assert ingestion.source_content_revision_id is None
    with pytest.raises(ValidationFailedError, match="source_content_revision_id"):
        _candidate(service, ingestion.id, "too-early")

    actor_id = ingestion.actor_id
    revision_id = _make_source_revision(
        db_session, actor_id=actor_id, source_id=ingestion.source_id
    )
    fixed = service.attach_source_content_revision(ingestion.id, revision_id)
    assert fixed.source_content_revision_id == revision_id
    cid = _candidate(service, ingestion.id, "after-fix")
    assert service.get_candidate(cid).candidate_key == "after-fix"

    other_rev = _make_source_revision(
        db_session, actor_id=actor_id, source_id=ingestion.source_id, body="other"
    )
    with pytest.raises(InvalidStateTransitionError, match="already fixed"):
        service.attach_source_content_revision(ingestion.id, other_rev)


def test_cross_ingestion_effect_and_clarification_rejected(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    a = _create_ingestion(db_session)
    b = _create_ingestion(db_session)
    foreign = _candidate(service, b.id, "foreign")

    with pytest.raises(ValidationFailedError, match="must belong to ingestion"):
        service.record_effect(
            ingestion_id=a.id,
            candidate_id=foreign,
            effect_key="claim:create",
            effect_type=KnowledgeIngestionEffectType.CREATE_ENTITY,
        )
    with pytest.raises(ValidationFailedError, match="must belong to the same ingestion"):
        service.record_clarification(
            ingestion_id=a.id,
            clarification_kind=KnowledgeIngestionClarificationKind.PACKAGE_LOCAL,
            question_payload={"q": "x"},
            root_candidate_id=foreign,
        )


def test_applied_effect_cannot_become_failed(db_session: Session) -> None:
    service = KnowledgeIngestionService(db_session)
    ingestion = _create_ingestion(db_session)
    cid = _candidate(service, ingestion.id, "mono")
    service.record_effect(
        ingestion_id=ingestion.id,
        candidate_id=cid,
        effect_key="statement:claimText",
        effect_type=KnowledgeIngestionEffectType.ASSERT_STATEMENT,
        status=KnowledgeIngestionEffectStatus.FAILED,
    )
    service.mark_effect_applied(
        ingestion_id=ingestion.id,
        candidate_id=cid,
        effect_key="statement:claimText",
    )
    with pytest.raises(InvalidStateTransitionError, match="cannot become failed"):
        service.mark_effect_failed(
            ingestion_id=ingestion.id,
            candidate_id=cid,
            effect_key="statement:claimText",
            error_code="oops",
        )
    row = service.get_effect_by_key(ingestion.id, cid, "statement:claimText")
    assert row is not None
    assert row.status == KnowledgeIngestionEffectStatus.APPLIED.value


def test_migration_head_includes_knowledge_ingestion_tables(db_session: Session) -> None:
    engine = db_session.get_bind()
    tables = set(inspect(engine).get_table_names())
    required = {
        "knowledge_ingestion",
        "knowledge_candidate",
        "knowledge_candidate_dependency",
        "knowledge_ingestion_clarification",
        "knowledge_ingestion_effect",
    }
    assert required.issubset(tables)
    version = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "e6f7a8b9c0d1"
