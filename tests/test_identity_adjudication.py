"""Bounded LLM identity adjudication (PR5)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from semantic_memory.config import Settings
from semantic_memory.models.enums import ActorType, AliasIdentityStrength, EntityStatus
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import (
    CreateEntityRequest,
    ExternalReferenceInput,
    ResolutionOutcome,
)
from semantic_memory.schemas.identity import (
    CandidateDecision,
    EvidenceStrength,
    IdentityCandidate,
    IdentityEvidence,
    IdentityResolutionOutcome,
)
from semantic_memory.schemas.identity_adjudication import (
    IdentityAdjudicationRequest,
    IdentityAdjudicationResult,
    IdentityCandidateAdjudication,
    IdentityLlmDecision,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.identity import IdentityService
from semantic_memory.services.identity_adjudication import (
    IdentityAdjudicationService,
    apply_adjudication_to_candidates,
    deterministic_blocks_llm_adjudication,
    llm_adjudication_may_run,
)
from semantic_memory.services.identity_evidence_package import package_identity_evidence
from semantic_memory.services.openai_identity_adjudicator import OpenAIIdentityAdjudicator


class _SpyAdjudicator:
    def __init__(self) -> None:
        self.calls = 0

    def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult:
        self.calls += 1
        return IdentityAdjudicationResult(provider="spy", model="spy")


class _PromoteFirstUncertainAdjudicator:
    def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult:
        uncertain = [c for c in request.candidates if c.decision == CandidateDecision.UNCERTAIN]
        if not uncertain:
            return IdentityAdjudicationResult(provider="test", model="test")
        return IdentityAdjudicationResult(
            provider="test",
            model="test",
            candidate_decisions=[
                IdentityCandidateAdjudication(
                    entity_id=uncertain[0].entity_id,
                    candidate_id="c1",
                    decision=CandidateDecision.SAME,
                    llm_decision=IdentityLlmDecision.SAME_ENTITY,
                    summary="test promotion",
                    cited_evidence_ids=["c1_e1"],
                )
            ],
        )


def _ensure_writer(session: Session) -> None:
    ActorService(session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))


def _uncertain_candidate(
    *,
    entity_id: uuid.UUID | None = None,
    name: str = "Didac",
    evidence_count: int = 2,
) -> IdentityCandidate:
    eid = entity_id or uuid.uuid4()
    reasons = [
        IdentityEvidence(
            signal="canonical_name_match" if i == 0 else "shared_neighbor",
            strength=EvidenceStrength.SUPPORTING,
            value=name if i == 0 else str(uuid.uuid4()),
            evidence_refs=[f"entity:{eid}", f"statement:{uuid.uuid4()}"],
        )
        for i in range(evidence_count)
    ]
    return IdentityCandidate(
        entity_id=eid,
        canonical_name=name,
        status=EntityStatus.ACTIVE,
        decision=CandidateDecision.UNCERTAIN,
        reasons=reasons,
    )


def test_llm_blocked_when_decisive_same_present() -> None:
    candidate = IdentityCandidate(
        entity_id=uuid.uuid4(),
        canonical_name="x",
        status=EntityStatus.ACTIVE,
        decision=CandidateDecision.SAME,
    )
    assert deterministic_blocks_llm_adjudication([candidate])
    assert not llm_adjudication_may_run(
        resolution=IdentityResolutionOutcome.AMBIGUOUS,
        candidates=[candidate],
    )


def test_apply_adjudication_only_touches_uncertain() -> None:
    entity_a = uuid.uuid4()
    entity_b = uuid.uuid4()
    candidates = [
        IdentityCandidate(
            entity_id=entity_a,
            canonical_name="A",
            status=EntityStatus.ACTIVE,
            decision=CandidateDecision.UNCERTAIN,
        ),
        IdentityCandidate(
            entity_id=entity_b,
            canonical_name="B",
            status=EntityStatus.ACTIVE,
            decision=CandidateDecision.DIFFERENT,
        ),
    ]
    merged = apply_adjudication_to_candidates(
        candidates,
        IdentityAdjudicationResult(
            candidate_decisions=[
                IdentityCandidateAdjudication(
                    entity_id=entity_a,
                    decision=CandidateDecision.SAME,
                ),
                IdentityCandidateAdjudication(
                    entity_id=entity_b,
                    decision=CandidateDecision.SAME,
                ),
            ]
        ),
    )
    assert merged[0].decision == CandidateDecision.SAME
    assert merged[1].decision == CandidateDecision.DIFFERENT


def test_evidence_package_caps_candidates_and_evidence() -> None:
    settings = Settings(
        identity_review_max_candidates=2,
        identity_review_max_evidence_per_candidate=3,
    )
    candidates = [_uncertain_candidate(evidence_count=5) for _ in range(4)]
    package = package_identity_evidence(
        incoming_canonical_name="Didac Cristobal",
        class_key="Person",
        candidates=candidates,
        settings=settings,
    )
    assert len(package.candidates) == 2
    assert package.truncated_candidates == 2
    assert all(len(c.evidence) == 3 for c in package.candidates)
    assert package.truncated_evidence == 4
    assert package.candidates[0].evidence[0].evidence_id == "c1_e1"


def test_external_reference_match_skips_adjudicator(db_session: Session) -> None:
    _ensure_writer(db_session)
    service = EntityService(db_session)
    ref = ExternalReferenceInput(source_system="crm", external_id="p-1")
    created = service.create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Didac",
            class_key="Person",
            external_reference=ref,
        )
    )
    assert created.entity is not None
    spy = _SpyAdjudicator()
    adjudication = IdentityAdjudicationService(
        adjudicator=spy,
        settings=Settings(identity_review_mode="mock"),
    )
    resolved = IdentityService(
        db_session,
        adjudication=adjudication,
    ).resolve(
        canonical_name="Someone Else",
        external_source_system="crm",
        external_id="p-1",
    )
    assert resolved.outcome == ResolutionOutcome.REUSE
    assert spy.calls == 0


def test_conflicting_decisive_candidates_skip_adjudicator(db_session: Session) -> None:
    _ensure_writer(db_session)
    person_class = OntologyRepository(db_session).get_class_by_key(
        namespace_key="core", class_key="Person"
    )
    assert person_class is not None
    actor = ActorService(db_session).require_active_actor("writer")
    repo = EntityRepository(db_session)
    left = repo.create(canonical_name="Alias Target", created_by_actor_id=actor.id)
    right = repo.create(canonical_name="Other", created_by_actor_id=actor.id)
    repo.add_type(entity_id=left.id, class_id=person_class.id, asserted_by_actor_id=actor.id)
    repo.add_type(entity_id=right.id, class_id=person_class.id, asserted_by_actor_id=actor.id)
    repo.add_alias(
        entity_id=left.id,
        alias="Shared Name",
        identity_strength=AliasIdentityStrength.AUTHORITATIVE.value,
    )
    repo.add_alias(
        entity_id=right.id,
        alias="Shared Name",
        identity_strength=AliasIdentityStrength.AUTHORITATIVE.value,
    )

    spy = _SpyAdjudicator()
    adjudication = IdentityAdjudicationService(
        adjudicator=spy,
        settings=Settings(identity_review_mode="mock"),
    )
    resolved = IdentityService(
        db_session,
        adjudication=adjudication,
    ).resolve(canonical_name="Shared Name", class_id=person_class.id)
    assert resolved.outcome == ResolutionOutcome.AMBIGUOUS
    assert spy.calls == 0


def test_adjudicator_may_promote_single_uncertain_to_match(db_session: Session) -> None:
    _ensure_writer(db_session)
    person_class = OntologyRepository(db_session).get_class_by_key(
        namespace_key="core", class_key="Person"
    )
    assert person_class is not None
    created = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Didac",
            class_key="Person",
        )
    )
    assert created.entity is not None

    adjudication = IdentityAdjudicationService(
        adjudicator=_PromoteFirstUncertainAdjudicator(),
        settings=Settings(identity_review_mode="mock"),
    )
    resolved = IdentityService(db_session, adjudication=adjudication).resolve(
        canonical_name="Didac",
        class_id=person_class.id,
    )
    assert resolved.outcome == ResolutionOutcome.REUSE
    assert resolved.entity is not None
    assert resolved.entity.id == created.entity.id
    assert resolved.identity is not None
    assert resolved.identity.metadata.get("adjudication") is not None


def test_shadow_mode_audits_without_enforcing_reuse(db_session: Session) -> None:
    _ensure_writer(db_session)
    person_class = OntologyRepository(db_session).get_class_by_key(
        namespace_key="core", class_key="Person"
    )
    assert person_class is not None
    created = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Didac",
            class_key="Person",
        )
    )
    assert created.entity is not None

    adjudication = IdentityAdjudicationService(
        adjudicator=_PromoteFirstUncertainAdjudicator(),
        settings=Settings(identity_review_mode="shadow"),
    )
    resolved = IdentityService(db_session, adjudication=adjudication).resolve(
        canonical_name="Didac",
        class_id=person_class.id,
    )
    assert resolved.outcome == ResolutionOutcome.AMBIGUOUS
    assert resolved.entity is None
    assert resolved.identity is not None
    audit = resolved.identity.metadata["adjudication"]
    assert audit["shadow"] is True
    assert audit["enforced"] is False
    assert audit["metadata"]["decisions"][0]["llm_decision"] == "SAME_ENTITY"
    assert audit["model"] == "test"


def test_provider_failure_leaves_uncertain(db_session: Session) -> None:
    _ensure_writer(db_session)
    person_class = OntologyRepository(db_session).get_class_by_key(
        namespace_key="core", class_key="Person"
    )
    assert person_class is not None
    EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Didac",
            class_key="Person",
        )
    )

    class _FailClosedWrapper:
        def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult:
            return IdentityAdjudicationResult(
                candidate_decisions=[
                    IdentityCandidateAdjudication(
                        entity_id=c.entity_id,
                        decision=CandidateDecision.UNCERTAIN,
                    )
                    for c in request.candidates
                ],
                provider="openai",
                model="gpt-5.6-terra",
                metadata={"fail_closed": True, "error": "provider_down"},
            )

    adjudication = IdentityAdjudicationService(
        adjudicator=_FailClosedWrapper(),
        settings=Settings(identity_review_mode="external"),
    )
    resolved = IdentityService(db_session, adjudication=adjudication).resolve(
        canonical_name="Didac",
        class_id=person_class.id,
    )
    assert resolved.outcome == ResolutionOutcome.AMBIGUOUS
    assert resolved.entity is None
    assert resolved.identity is not None
    audit = resolved.identity.metadata["adjudication"]
    assert audit["metadata"]["fail_closed"] is True
    assert all(row["decision"] == "UNCERTAIN" for row in audit["metadata"]["decisions"])


def test_openai_malformed_response_fail_closed() -> None:
    settings = Settings(
        identity_review_mode="external",
        identity_review_api_key="test-key",
        identity_review_api_base="https://example.test/v1",
    )
    adjudicator = OpenAIIdentityAdjudicator(settings)
    candidate = _uncertain_candidate()
    request = IdentityAdjudicationRequest(
        incoming_canonical_name="Didac Cristobal",
        class_key="Person",
        candidates=[candidate],
    )
    malformed = {
        "id": "chatcmpl-x",
        "choices": [{"message": {"content": "{not-json"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps(malformed).encode()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake_resp):
        result = adjudicator.adjudicate(request)
    assert len(result.candidate_decisions) == 1
    assert result.candidate_decisions[0].decision == CandidateDecision.UNCERTAIN
    assert result.metadata["calls"][0]["fail_closed"] is True
    assert "malformed" in result.metadata["calls"][0]["error"]


def test_openai_timeout_fail_closed() -> None:
    settings = Settings(
        identity_review_mode="external",
        identity_review_api_key="test-key",
        identity_review_api_base="https://example.test/v1",
    )
    adjudicator = OpenAIIdentityAdjudicator(settings)
    request = IdentityAdjudicationRequest(
        incoming_canonical_name="Didac",
        candidates=[_uncertain_candidate()],
    )
    with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
        result = adjudicator.adjudicate(request)
    assert result.candidate_decisions[0].decision == CandidateDecision.UNCERTAIN
    assert result.metadata["calls"][0]["fail_closed"] is True


def test_openai_success_maps_same_and_strips_invented_evidence() -> None:
    settings = Settings(
        identity_review_mode="external",
        identity_review_api_key="test-key",
        identity_review_api_base="https://example.test/v1",
    )
    adjudicator = OpenAIIdentityAdjudicator(settings)
    candidate = _uncertain_candidate(evidence_count=2)
    request = IdentityAdjudicationRequest(
        incoming_canonical_name="Didac Cristobal",
        class_key="Person",
        candidates=[candidate],
    )
    body = {
        "id": "chatcmpl-ok",
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "candidates": [
                                {
                                    "candidate_id": "c1",
                                    "decision": "SAME_ENTITY",
                                    "summary": "shared neighbor evidence",
                                    "cited_evidence_ids": ["c1_e1", "invented_e99"],
                                }
                            ]
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 20},
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps(body).encode()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake_resp):
        result = adjudicator.adjudicate(request)
    row = result.candidate_decisions[0]
    assert row.decision == CandidateDecision.SAME
    assert row.llm_decision == IdentityLlmDecision.SAME_ENTITY
    assert row.cited_evidence_ids == ["c1_e1"]
    assert row.rejected_invented_evidence_ids == ["invented_e99"]
    assert result.metadata["input_tokens"] == 40
    assert result.metadata["output_tokens"] == 20
