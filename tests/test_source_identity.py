"""Source-system registry and resolve_source identity tests (PR2)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from semantic_memory.exceptions import AmbiguousSourceError
from semantic_memory.models import ActorType, Source
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.provenance import EnsureSourceRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.source_identity import (
    SourceIdentityService,
    SourceSystemRegistryService,
    normalize_source_system_key,
)


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def test_normalize_source_system_key() -> None:
    assert normalize_source_system_key("Airbus Confluence") == "airbus_confluence"
    assert normalize_source_system_key("  confluence ") == "confluence"


def test_registry_maps_aliases_to_confluence(db_session: Session) -> None:
    registry = SourceSystemRegistryService(db_session)
    assert registry.canonicalize("Airbus Confluence") == "confluence"
    assert registry.canonicalize("Confluence") == "confluence"
    assert registry.canonicalize("confluence") == "confluence"
    assert registry.canonicalize("atlassian_confluence") == "confluence"
    assert registry.canonicalize("Notion") == "notion"  # provisional


def test_alias_equivalent_source_systems_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    provenance = ProvenanceService(db_session)

    first = provenance.ensure_source(
        EnsureSourceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_system="Airbus Confluence",
            external_id="878906633",
            title="Divide and Conquer",
        )
    )
    assert first.outcome.value == "CREATE"
    assert first.source.canonical_source_system == "confluence"
    assert first.source.source_system == "confluence"
    assert first.source.metadata.get("source_system_raw") == "Airbus Confluence"

    second = provenance.ensure_source(
        EnsureSourceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_system="confluence",
            external_id="878906633",
            title="Divide and Conquer again",
        )
    )
    assert second.outcome.value == "REUSE"
    assert second.source.id == first.source.id

    third = provenance.ensure_source(
        EnsureSourceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_system="atlassian_confluence",
            external_id="878906633",
        )
    )
    assert third.reused is True
    assert third.source.id == first.source.id


def test_resolve_source_match_no_match(db_session: Session) -> None:
    _ensure_writer(db_session)
    ProvenanceService(db_session).ensure_source(
        EnsureSourceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_system="Confluence",
            external_id="page-42",
        )
    )
    identity = SourceIdentityService(db_session)
    matched = identity.resolve(source_system="Airbus Confluence", external_id="page-42")
    assert matched.resolution.value == "MATCH"
    assert matched.source is not None

    missing = identity.resolve(source_system="Airbus Confluence", external_id="missing")
    assert missing.resolution.value == "NO_MATCH"
    assert missing.canonical_source_system == "confluence"


def test_existing_duplicates_are_ambiguous_not_auto_merged(db_session: Session) -> None:
    _ensure_writer(db_session)
    actor = ActorService(db_session).require_active_actor("writer")

    # Simulate a pre-resolution collision (as migration would flag).
    a = Source(
        id=uuid.uuid4(),
        source_system="Airbus Confluence",
        canonical_source_system="confluence",
        external_id="dup-1",
        identity_conflict=True,
        metadata_json={},
        created_by_actor_id=actor.id,
    )
    b = Source(
        id=uuid.uuid4(),
        source_system="confluence",
        canonical_source_system="confluence",
        external_id="dup-1",
        identity_conflict=True,
        metadata_json={},
        created_by_actor_id=actor.id,
    )
    db_session.add_all([a, b])
    db_session.flush()

    identity = SourceIdentityService(db_session)
    result = identity.resolve(source_system="Confluence", external_id="dup-1")
    assert result.resolution.value == "AMBIGUOUS"
    assert len(result.candidates) == 2

    with pytest.raises(AmbiguousSourceError) as exc:
        ProvenanceService(db_session).ensure_source(
            EnsureSourceRequest(
                actor_key="writer",
                request_id=uuid.uuid4(),
                idempotency_key=str(uuid.uuid4()),
                source_system="Airbus Confluence",
                external_id="dup-1",
            )
        )
    assert exc.value.error_code == "AMBIGUOUS_SOURCE"


def test_unique_index_blocks_second_non_conflict_insert(db_session: Session) -> None:
    _ensure_writer(db_session)
    provenance = ProvenanceService(db_session)
    provenance.ensure_source(
        EnsureSourceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source_system="confluence",
            external_id="uniq-9",
        )
    )
    actor = ActorService(db_session).require_active_actor("writer")
    db_session.add(
        Source(
            id=uuid.uuid4(),
            source_system="confluence",
            canonical_source_system="confluence",
            external_id="uniq-9",
            identity_conflict=False,
            metadata_json={},
            created_by_actor_id=actor.id,
        )
    )
    with pytest.raises(Exception):
        db_session.flush()


def test_migration_seeded_registry_present(db_session: Session) -> None:
    count = db_session.execute(
        text("SELECT COUNT(*) FROM source_system_registry WHERE canonical_key = 'confluence'")
    ).scalar_one()
    assert count == 1
    aliases = db_session.execute(
        text(
            "SELECT COUNT(*) FROM source_system_alias a "
            "JOIN source_system_registry r ON r.id = a.registry_id "
            "WHERE r.canonical_key = 'confluence'"
        )
    ).scalar_one()
    assert aliases >= 3
