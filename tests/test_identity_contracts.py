"""Identity-resolution contract and alias-strength persistence tests (PR1)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from semantic_memory.models import ActorType, AliasIdentityStrength, EntityAlias
from semantic_memory.models.enums import EntityStatus
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import (
    AddEntityAliasRequest,
    CreateEntityRequest,
    ResolutionOutcome,
)
from semantic_memory.schemas.identity import (
    CandidateDecision,
    EvidenceStrength,
    IdentityAction,
    IdentityCandidate,
    IdentityEvidence,
    IdentityResolutionOutcome,
    action_for_resolution,
    aggregate_candidate_decisions,
    from_legacy_resolution_outcome,
    to_legacy_resolution_outcome,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def test_legacy_resolution_outcome_round_trip() -> None:
    assert to_legacy_resolution_outcome(IdentityResolutionOutcome.MATCH) == ResolutionOutcome.REUSE
    assert (
        to_legacy_resolution_outcome(IdentityResolutionOutcome.NO_MATCH) == ResolutionOutcome.CREATE
    )
    assert (
        to_legacy_resolution_outcome(IdentityResolutionOutcome.AMBIGUOUS)
        == ResolutionOutcome.AMBIGUOUS
    )

    assert from_legacy_resolution_outcome(ResolutionOutcome.REUSE) == (
        IdentityResolutionOutcome.MATCH,
        IdentityAction.REUSE,
    )
    assert from_legacy_resolution_outcome(ResolutionOutcome.CREATE) == (
        IdentityResolutionOutcome.NO_MATCH,
        IdentityAction.CREATE,
    )
    assert from_legacy_resolution_outcome(ResolutionOutcome.AMBIGUOUS) == (
        IdentityResolutionOutcome.AMBIGUOUS,
        IdentityAction.CLARIFY,
    )


def test_action_for_resolution_mapping() -> None:
    assert action_for_resolution(IdentityResolutionOutcome.MATCH) == IdentityAction.REUSE
    assert action_for_resolution(IdentityResolutionOutcome.NO_MATCH) == IdentityAction.CREATE
    assert action_for_resolution(IdentityResolutionOutcome.AMBIGUOUS) == IdentityAction.CLARIFY


def test_aggregate_candidate_decisions() -> None:
    def cand(decision: CandidateDecision) -> IdentityCandidate:
        return IdentityCandidate(
            entity_id=uuid.uuid4(),
            canonical_name="x",
            status=EntityStatus.ACTIVE,
            decision=decision,
        )

    assert aggregate_candidate_decisions([]) == IdentityResolutionOutcome.NO_MATCH
    assert (
        aggregate_candidate_decisions([cand(CandidateDecision.DIFFERENT)])
        == IdentityResolutionOutcome.NO_MATCH
    )
    assert (
        aggregate_candidate_decisions([cand(CandidateDecision.SAME)])
        == IdentityResolutionOutcome.MATCH
    )
    assert (
        aggregate_candidate_decisions(
            [cand(CandidateDecision.SAME), cand(CandidateDecision.SAME)]
        )
        == IdentityResolutionOutcome.AMBIGUOUS
    )
    assert (
        aggregate_candidate_decisions(
            [cand(CandidateDecision.DIFFERENT), cand(CandidateDecision.UNCERTAIN)]
        )
        == IdentityResolutionOutcome.AMBIGUOUS
    )


def test_identity_evidence_requires_no_score_and_keeps_refs() -> None:
    evidence = IdentityEvidence(
        signal="external_reference_match",
        strength=EvidenceStrength.DECISIVE,
        namespace="employee_id",
        value="12345",
        evidence_refs=["statement:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
    )
    dumped = evidence.model_dump()
    assert "score" not in dumped
    assert "confidence" not in dumped
    assert dumped["evidence_refs"] == ["statement:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    assert dumped["strength"] == "decisive"


def test_new_aliases_default_to_supporting(db_session: Session) -> None:
    _ensure_writer(db_session)
    created = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Didac Contract",
            class_key="Person",
            aliases=["D. Contract"],
        )
    )
    assert created.entity is not None
    assert created.entity.aliases
    assert all(
        entry.identity_strength == AliasIdentityStrength.SUPPORTING
        for entry in created.entity.alias_entries
    )
    rows = list(
        db_session.scalars(
            select(EntityAlias).where(EntityAlias.entity_id == created.entity.id)
        ).all()
    )
    assert rows
    assert all(row.identity_strength == AliasIdentityStrength.SUPPORTING.value for row in rows)


def test_add_alias_can_set_authoritative_without_resolver_change(
    db_session: Session,
) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    created = service.create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Identity Strength Person",
            class_key="Person",
        )
    )
    assert created.entity is not None
    updated = service.add_entity_alias(
        AddEntityAliasRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            entity_id=created.entity.id,
            alias="Confirmed Name",
            identity_strength=AliasIdentityStrength.AUTHORITATIVE,
        )
    )
    by_alias = {entry.alias: entry.identity_strength for entry in updated.alias_entries}
    assert by_alias["Confirmed Name"] == AliasIdentityStrength.AUTHORITATIVE
    assert by_alias[created.entity.canonical_name] == AliasIdentityStrength.SUPPORTING


def test_identity_strength_check_constraint(db_session: Session) -> None:
    _ensure_writer(db_session)
    created = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Constraint Person",
            class_key="Person",
        )
    )
    assert created.entity is not None
    with pytest.raises(Exception):
        db_session.execute(
            text(
                "INSERT INTO entity_alias "
                "(id, entity_id, alias, normalized_alias, identity_strength) "
                "VALUES (:id, :entity_id, :alias, :normalized, :strength)"
            ),
            {
                "id": uuid.uuid4(),
                "entity_id": created.entity.id,
                "alias": "Bad Strength",
                "normalized": "bad strength",
                "strength": "maybe",
            },
        )
        db_session.flush()


def test_backfilled_aliases_are_supporting(db_session: Session) -> None:
    """After migration, every alias row has a non-null supporting|authoritative strength."""
    rows = db_session.execute(
        text(
            "SELECT COUNT(*) FROM entity_alias "
            "WHERE identity_strength IS NULL "
            "   OR identity_strength NOT IN ('supporting', 'authoritative')"
        )
    ).scalar_one()
    assert rows == 0
