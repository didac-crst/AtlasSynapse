"""Phase C: YAML/JSON → staged knowledge_candidate extraction."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    InvalidStateTransitionError,
    KnowledgeExtractionConflictError,
    UnsupportedSourceFormatError,
)
from semantic_memory.models import ActorType, Source
from semantic_memory.models.enums import (
    KnowledgeCandidateDerivation,
    KnowledgeCandidateEpistemicStatus,
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    KnowledgeCandidateState,
    KnowledgeIngestionMode,
    KnowledgeIngestionStatus,
)
from semantic_memory.repositories.provenance import ProvenanceRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.knowledge_extraction import KnowledgeExtractionService
from semantic_memory.services.knowledge_extraction.candidate_keys import (
    build_candidate_key,
    semantic_fingerprint,
)
from semantic_memory.services.knowledge_extraction.formats import (
    KnowledgePackageFormat,
    detect_package_format,
)
from semantic_memory.services.knowledge_extraction.structural import parse_structured_source
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "knowledge_ingestion"
    / "strategy_package_v1.yaml"
)


def _actor_source(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    actor = ActorService(session).ensure(
        ActorEnsureRequest(key=f"extract-{uuid.uuid4().hex[:8]}", actor_type=ActorType.AGENT)
    )
    source = Source(
        id=uuid.uuid4(),
        source_system="test",
        external_id=f"strategy-{uuid.uuid4().hex[:8]}",
        metadata_json={},
        created_by_actor_id=actor.id,
    )
    session.add(source)
    session.flush()
    return actor.id, source.id


def _ingestion_with_content(
    session: Session,
    content: str,
    *,
    mode: KnowledgeIngestionMode = KnowledgeIngestionMode.EXECUTE,
    package_format: str = "yaml",
) -> uuid.UUID:
    actor_id, source_id = _actor_source(session)
    rev = ProvenanceRepository(session).create_content_revision(
        source_id=source_id,
        revision_number=1,
        canonical_content=content,
        canonical_format="text",
        canonical_content_hash=hashlib.sha256(content.encode()).hexdigest(),
        captured_at=datetime.now(UTC),
        created_by_actor_id=actor_id,
        metadata_json={"package_format": package_format},
    )
    service = KnowledgeIngestionService(session)
    ingestion = service.create_ingestion(
        actor_id=actor_id,
        source_id=source_id,
        source_hash=rev.canonical_content_hash,
        request_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        pipeline_version="ki-extract-v1",
        ontology_revision_marker="core@test",
        mode=mode,
        source_content_revision_id=rev.id,
    )
    return ingestion.id


def test_detect_rejects_unsupported_formats() -> None:
    with pytest.raises(UnsupportedSourceFormatError):
        detect_package_format("<html><body>hi</body></html>")


def test_structural_parser_preserves_paths_and_yaml_lines() -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    parsed = parse_structured_source(content, package_format=KnowledgePackageFormat.YAML)
    paths = {tuple(f.path_list()) for f in parsed.fragments}
    assert ("differentiation", "rejected_or_weakened_hypotheses", 0) in paths
    assert ("open_questions", 0) in paths
    leaf = next(
        f
        for f in parsed.fragments
        if f.path_list() == ["differentiation", "rejected_or_weakened_hypotheses", 0]
    )
    assert leaf.raw_value == "Open-ended knowledge is unique"
    assert leaf.start_line is not None
    assert leaf.structural_type == "string"


def test_strategy_package_extraction_semantics(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    ingestion_id = _ingestion_with_content(db_session, content)
    stats = KnowledgeExtractionService(db_session).extract(ingestion_id)

    assert stats.candidates_created >= 10
    assert stats.fragments_skipped >= 1
    assert stats.package_format == "yaml"
    assert "metadata" in stats.skipped_by_reason
    assert "container" in stats.skipped_by_reason
    assert "unsupported_semantics" in stats.skipped_by_reason

    service = KnowledgeIngestionService(db_session)
    candidates = service.list_candidates(ingestion_id)
    assert all(c.state == KnowledgeCandidateState.EXTRACTED.value for c in candidates)

    by_text = {c.claim_text: c for c in candidates if c.claim_text}

    rejected = by_text["Open-ended knowledge is unique"]
    assert rejected.kind == KnowledgeCandidateKind.HYPOTHESIS.value
    assert rejected.polarity == KnowledgeCandidatePolarity.POSITIVE.value
    assert rejected.epistemic_status == KnowledgeCandidateEpistemicStatus.REJECTED.value
    assert "rejected_or_weakened_hypotheses" in rejected.source_context_path

    rejected_named = by_text["Memory without governance is sufficient"]
    assert rejected_named.kind == KnowledgeCandidateKind.HYPOTHESIS.value
    assert rejected_named.epistemic_status == KnowledgeCandidateEpistemicStatus.REJECTED.value

    question = by_text["What recurring enterprise need would justify a commercial offering?"]
    assert question.kind == KnowledgeCandidateKind.QUESTION.value
    assert question.epistemic_status == KnowledgeCandidateEpistemicStatus.OPEN.value

    neg = by_text["AtlasSynapse should not replace a CRM"]
    assert neg.kind == KnowledgeCandidateKind.RECOMMENDATION.value
    assert neg.polarity == KnowledgeCandidatePolarity.NEGATIVE.value
    assert neg.epistemic_status == KnowledgeCandidateEpistemicStatus.ACTIVE.value

    pos = by_text["AtlasSynapse should publish an open reference implementation"]
    assert pos.kind == KnowledgeCandidateKind.RECOMMENDATION.value
    assert pos.polarity == KnowledgeCandidatePolarity.POSITIVE.value

    license_rec = next(c for c in candidates if c.claim_text and "Apache-2.0" in c.claim_text)
    assert license_rec.kind == KnowledgeCandidateKind.RECOMMENDATION.value
    assert license_rec.derivation in {
        KnowledgeCandidateDerivation.NORMALIZED.value,
        KnowledgeCandidateDerivation.EXPLICIT.value,
    }

    assertion = by_text["AtlasSynapse differentiates through semantic governance"]
    assert assertion.kind == KnowledgeCandidateKind.ASSERTION.value
    assert assertion.epistemic_status == KnowledgeCandidateEpistemicStatus.ACTIVE.value

    # Rejected alternative is never an active positive assertion.
    agpl = by_text["AGPL"]
    assert not (
        agpl.kind == "assertion"
        and agpl.polarity == "positive"
        and agpl.epistemic_status == "active"
    )
    assert agpl.epistemic_status == KnowledgeCandidateEpistemicStatus.REJECTED.value

    # Explicitly marked example leaf may extract; bare examples/caveats/anti-use do not.
    assert "A company could use AtlasSynapse to track warranty information." not in by_text
    assert "Large enterprises may require more governance." not in by_text
    assert "AtlasSynapse replaces a CRM for all account management." not in by_text
    assert "Explicit example claim should still extract when marked" in by_text

    texts = {c.claim_text for c in candidates}
    assert "atlas-strategy-v1" not in texts
    assert "====" not in texts

    ingestion = service.get_ingestion(ingestion_id)
    assert ingestion.status == KnowledgeIngestionStatus.RESOLVING.value
    assert ingestion.stats_json["extraction"]["candidates_created"] == stats.candidates_created
    assert ingestion.stats_json["extraction"]["skipped_by_reason"]["unsupported_semantics"] >= 1


def test_extraction_counts_by_kind_status_derivation(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    ingestion_id = _ingestion_with_content(db_session, content)
    KnowledgeExtractionService(db_session).extract(ingestion_id)
    candidates = KnowledgeIngestionService(db_session).list_candidates(ingestion_id)

    kinds = {k.value: 0 for k in KnowledgeCandidateKind}
    statuses = {s.value: 0 for s in KnowledgeCandidateEpistemicStatus}
    derivations = {d.value: 0 for d in KnowledgeCandidateDerivation}
    for row in candidates:
        kinds[row.kind] += 1
        statuses[row.epistemic_status] += 1
        derivations[row.derivation] += 1

    assert kinds["hypothesis"] >= 2
    assert kinds["recommendation"] >= 3
    assert kinds["question"] >= 2
    assert kinds["assertion"] >= 1
    assert statuses["rejected"] >= 2
    assert statuses["open"] >= 2
    assert statuses["active"] >= 1
    assert derivations["explicit"] + derivations["normalized"] + derivations["inferred"] == len(
        candidates
    )


def test_reextraction_is_idempotent(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    ingestion_id = _ingestion_with_content(db_session, content)
    svc = KnowledgeExtractionService(db_session)
    first = svc.extract(ingestion_id)
    # Retry from resolving (allowed explicit re-extract path).
    second = svc.extract(ingestion_id)
    assert second.candidates_created == 0
    assert second.candidates_reused == first.candidates_created
    candidates = KnowledgeIngestionService(db_session).list_candidates(ingestion_id)
    assert len(candidates) == first.candidates_created


def test_structural_key_conflicts_on_semantic_change(db_session: Session) -> None:
    """Same path/ordinal slot + changed wording/kind must conflict, not duplicate."""
    content = "claims:\n  - statement: AtlasSynapse differentiates through semantic governance\n"
    ingestion_id = _ingestion_with_content(db_session, content)
    KnowledgeExtractionService(db_session).extract(ingestion_id)
    service = KnowledgeIngestionService(db_session)
    cand = service.list_candidates(ingestion_id)[0]
    path = list(cand.source_context_path)
    key = build_candidate_key(source_context_path=path, ordinal=0)
    assert cand.candidate_key == key

    # Mutate staged semantics under the same structural key.
    service.update_candidate_epistemic_status(cand.id, KnowledgeCandidateEpistemicStatus.REJECTED)
    with pytest.raises(KnowledgeExtractionConflictError) as exc:
        KnowledgeExtractionService(db_session).extract(ingestion_id)
    assert exc.value.details["candidate_key"] == key
    # Still a single candidate row — no duplicate slot.
    assert len(service.list_candidates(ingestion_id)) == 1


def test_examples_and_caveats_do_not_become_assertions(db_session: Session) -> None:
    content = """
package_id: contextual-demo
examples:
  - A company could use AtlasSynapse to track warranty information.
caveats:
  - Large enterprises may require more governance.
anti_use_cases:
  - AtlasSynapse replaces a CRM for all account management.
rejected_options:
  - AGPL
claims:
  - statement: AtlasSynapse differentiates through semantic governance
"""
    ingestion_id = _ingestion_with_content(db_session, content)
    KnowledgeExtractionService(db_session).extract(ingestion_id)
    candidates = KnowledgeIngestionService(db_session).list_candidates(ingestion_id)
    texts = {c.claim_text for c in candidates}

    assert "A company could use AtlasSynapse to track warranty information." not in texts
    assert "Large enterprises may require more governance." not in texts
    assert "AtlasSynapse replaces a CRM for all account management." not in texts
    assert "AtlasSynapse differentiates through semantic governance" in texts

    agpl = next(c for c in candidates if c.claim_text == "AGPL")
    assert agpl.epistemic_status == "rejected"
    assert not (
        agpl.kind == "assertion"
        and agpl.polarity == "positive"
        and agpl.epistemic_status == "active"
    )


def test_extraction_failure_retains_candidates_and_allows_retry(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    ingestion_id = _ingestion_with_content(db_session, content)
    svc = KnowledgeExtractionService(db_session)
    first = svc.extract(ingestion_id)
    service = KnowledgeIngestionService(db_session)

    # Simulate a mid-flight failure after candidates exist.
    row = service.get_ingestion(ingestion_id)
    service._repo.update_ingestion(  # noqa: SLF001 — test failure semantics
        row,
        status=KnowledgeIngestionStatus.FAILED.value,
        stats_json={
            **dict(row.stats_json or {}),
            "extraction_error": {"type": "SimulatedError", "message": "boom"},
        },
    )
    retained = len(service.list_candidates(ingestion_id))
    assert retained == first.candidates_created

    # Explicit retry from failed reuses identical candidates.
    second = svc.extract(ingestion_id)
    assert second.candidates_created == 0
    assert second.candidates_reused == retained
    assert len(service.list_candidates(ingestion_id)) == retained
    assert service.get_ingestion(ingestion_id).status == KnowledgeIngestionStatus.RESOLVING.value
    assert "extraction_error" not in service.get_ingestion(ingestion_id).stats_json


def test_extraction_rejects_illegal_start_status(db_session: Session) -> None:
    content = "open_questions:\n  - Why?\n"
    ingestion_id = _ingestion_with_content(db_session, content)
    service = KnowledgeIngestionService(db_session)
    service.set_ingestion_status(ingestion_id, KnowledgeIngestionStatus.COMMITTING)
    with pytest.raises(InvalidStateTransitionError, match="accepted|extracting|resolving|failed"):
        KnowledgeExtractionService(db_session).extract(ingestion_id)


def test_dry_run_extracts_candidates_without_effects(db_session: Session) -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    ingestion_id = _ingestion_with_content(db_session, content, mode=KnowledgeIngestionMode.DRY_RUN)
    stats = KnowledgeExtractionService(db_session).extract(ingestion_id)
    service = KnowledgeIngestionService(db_session)
    assert stats.candidates_created > 0
    assert len(service.list_candidates(ingestion_id)) == stats.candidates_created
    assert service.list_effects(ingestion_id) == []
    assert service.get_ingestion(ingestion_id).status == KnowledgeIngestionStatus.RESOLVING.value


def test_candidate_key_is_structural_not_semantic() -> None:
    path = ["open_questions", 0]
    a = build_candidate_key(source_context_path=path, ordinal=0)
    b = build_candidate_key(source_context_path=path, ordinal=0)
    c = build_candidate_key(source_context_path=path, ordinal=1)
    assert a == b
    assert a != c
    assert a.startswith("c_")

    fp1 = semantic_fingerprint(
        kind="assertion",
        polarity="positive",
        epistemic_status="active",
        derivation="explicit",
        claim_text="AtlasSynapse differentiates through semantic governance",
        claim_payload={"raw": "x", "classification_basis": ["ignored"]},
    )
    fp2 = semantic_fingerprint(
        kind="hypothesis",
        polarity="positive",
        epistemic_status="active",
        derivation="explicit",
        claim_text="AtlasSynapse may differentiate through semantic governance",
        claim_payload={"raw": "x"},
    )
    # Same structural key would apply; fingerprints differ → conflict path.
    assert fp1 != fp2
    # Volatile classification_basis does not affect fingerprint.
    fp1b = semantic_fingerprint(
        kind="assertion",
        polarity="positive",
        epistemic_status="active",
        derivation="explicit",
        claim_text="AtlasSynapse differentiates through semantic governance",
        claim_payload={"raw": "x", "classification_basis": ["other"]},
    )
    assert fp1 == fp1b


def test_json_package_extraction(db_session: Session) -> None:
    content = """
    {
      "package_id": "json-demo",
      "recommendations": ["AtlasSynapse should not replace a CRM"],
      "open_questions": ["Which license should AtlasSynapse use?"]
    }
    """
    ingestion_id = _ingestion_with_content(db_session, content, package_format="json")
    stats = KnowledgeExtractionService(db_session).extract(ingestion_id)
    candidates = KnowledgeIngestionService(db_session).list_candidates(ingestion_id)
    assert stats.package_format == "json"
    assert any(c.kind == "recommendation" and c.polarity == "negative" for c in candidates)
    assert any(c.kind == "question" and c.epistemic_status == "open" for c in candidates)
