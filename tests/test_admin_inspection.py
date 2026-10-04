"""Admin inspection API tests (Phase 15b)."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from semantic_memory.api.app import create_app
from semantic_memory.config import Settings, get_settings
from semantic_memory.db import get_db_session, reset_engine
from semantic_memory.models import ActorType, BatchStatus
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import (
    FeedbackSeverity,
    FeedbackType,
    LlmCallStatus,
    LlmCostStatus,
    OperationStatus,
    ProposalStatus,
    ProposalType,
)
from semantic_memory.repositories.batches import BatchRepository
from semantic_memory.repositories.conflicts import ConflictRepository
from semantic_memory.repositories.feedback import FeedbackRepository
from semantic_memory.repositories.governance import GovernanceRepository
from semantic_memory.repositories.llm_calls import LlmCallLogRepository
from semantic_memory.repositories.operations import OperationLogRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.ontology import OntologyService


@pytest.fixture
def admin_client(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    get_settings.cache_clear()
    reset_engine()
    monkeypatch.setenv("HTTP_API_TOKEN", "secret-api-token")
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")
    monkeypatch.setenv("APP_ENV", "development")
    settings = Settings()
    app = create_app(settings)

    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)

    def _override_db() -> Generator[Session, None, None]:
        session = SessionLocal()
        session.begin_nested()

        from sqlalchemy import event

        @event.listens_for(session, "after_transaction_end")
        def _restart_savepoint(sess: Session, trans: object) -> None:
            if getattr(trans, "nested", False) and not getattr(
                getattr(trans, "_parent", None), "nested", True
            ):
                sess.begin_nested()

        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    if transaction.is_active:
        transaction.rollback()
    connection.close()
    get_settings.cache_clear()
    reset_engine()


def _api_headers() -> dict[str, str]:
    return {"Authorization": "Bearer secret-api-token"}


def _admin_headers() -> dict[str, str]:
    return {
        **_api_headers(),
        "X-Admin-Token": "test-admin-token",
    }


def _seed_actor(session: Session, key: str = "admin-inspect-actor") -> uuid.UUID:
    actor = ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return actor.id


def test_admin_auth_layers(admin_client: TestClient) -> None:
    no_api = admin_client.get("/v1/admin/summary")
    assert no_api.status_code == 401

    bad_api = admin_client.get(
        "/v1/admin/summary",
        headers={"Authorization": "Bearer wrong"},
    )
    assert bad_api.status_code == 401

    no_admin = admin_client.get("/v1/admin/summary", headers=_api_headers())
    assert no_admin.status_code == 403

    bad_admin = admin_client.get(
        "/v1/admin/summary",
        headers={**_api_headers(), "X-Admin-Token": "nope"},
    )
    assert bad_admin.status_code == 403

    ok = admin_client.get("/v1/admin/summary", headers=_admin_headers())
    assert ok.status_code == 200


def test_limit_and_date_range_validation(admin_client: TestClient) -> None:
    oversized = admin_client.get(
        "/v1/admin/operations",
        headers=_admin_headers(),
        params={"limit": 201},
    )
    assert oversized.status_code == 422

    inverted = admin_client.get(
        "/v1/admin/operations",
        headers=_admin_headers(),
        params={
            "created_after": "2026-10-04T12:00:00Z",
            "created_before": "2026-10-04T11:00:00Z",
        },
    )
    assert inverted.status_code == 422
    assert inverted.json()["error_code"] == "VALIDATION_FAILED"


def test_operations_list_omits_payloads_and_orders(admin_client: TestClient) -> None:
    ensure = admin_client.post(
        "/v1/actors/ensure",
        headers={**_admin_headers()},
        json={
            "key": "ops-writer",
            "actor_type": "agent",
            "capabilities": [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            "status": "active",
        },
    )
    assert ensure.status_code == 200

    created = admin_client.post(
        "/v1/entities",
        headers=_api_headers(),
        json={
            "actor_key": "ops-writer",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "canonical_name": f"Inspect Person {uuid.uuid4()}",
            "class_key": "Person",
        },
    )
    assert created.status_code == 200

    listed = admin_client.get(
        "/v1/admin/operations",
        headers=_admin_headers(),
        params={"actor_key": "ops-writer", "operation_name": "create_entity"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 1
    assert "limit" in body and "offset" in body
    first = body["items"][0]
    assert "request_payload" not in first
    assert "response_payload" not in first
    assert first["operation_name"] == "create_entity"

    detail = admin_client.get(
        f"/v1/admin/operations/{first['id']}",
        headers=_admin_headers(),
    )
    assert detail.status_code == 200
    assert detail.json().get("request_payload") is None

    with_payloads = admin_client.get(
        f"/v1/admin/operations/{first['id']}",
        headers=_admin_headers(),
        params={"include_payloads": "true"},
    )
    assert with_payloads.status_code == 200
    assert with_payloads.json().get("request_payload") is not None

    # Deterministic ordering: created_at desc, id desc
    ids = [item["id"] for item in body["items"]]
    created_ats = [item["created_at"] for item in body["items"]]
    paired = list(zip(created_ats, ids, strict=True))
    assert paired == sorted(paired, key=lambda row: (row[0], row[1]), reverse=True)


def test_actor_key_and_id_equivalent(admin_client: TestClient) -> None:
    ensure = admin_client.post(
        "/v1/actors/ensure",
        headers=_admin_headers(),
        json={
            "key": "equiv-actor",
            "actor_type": "agent",
            "capabilities": [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            "status": "active",
        },
    )
    assert ensure.status_code == 200
    actor_id = ensure.json()["id"]

    by_key = admin_client.get(
        "/v1/admin/operations",
        headers=_admin_headers(),
        params={"actor_key": "equiv-actor"},
    )
    by_id = admin_client.get(
        "/v1/admin/operations",
        headers=_admin_headers(),
        params={"actor_id": actor_id},
    )
    assert by_key.status_code == 200
    assert by_id.status_code == 200
    assert by_key.json()["total"] == by_id.json()["total"]


def test_summary_costs_and_feedback_aggregates(db_session: Session) -> None:
    actor_id = _seed_actor(db_session, "summary-actor")
    ops = OperationLogRepository(db_session)
    failed = ops.start(
        actor_id=actor_id,
        operation_name="assert_statement",
        request_id=uuid.uuid4(),
        trace_id=None,
        idempotency_key="k1",
        request_payload={"secret": "x"},
    )
    ops.finish(failed, status=OperationStatus.FAILED, error_code="INTERNAL_ERROR")
    rejected = ops.start(
        actor_id=actor_id,
        operation_name="create_entity",
        request_id=uuid.uuid4(),
        trace_id=None,
        idempotency_key="k2",
        request_payload=None,
    )
    ops.finish(
        rejected,
        status=OperationStatus.REJECTED,
        error_code="UNKNOWN_PREDICATE",
    )

    llm = LlmCallLogRepository(db_session)
    usd = llm.start(provider="mock", model="m", purpose="semantic_review", actor_id=actor_id)
    llm.finish(
        usd,
        status=LlmCallStatus.SUCCEEDED,
        outcome="accept",
        input_tokens=10,
        output_tokens=5,
        cost_amount=Decimal("1.25"),
        cost_currency="USD",
        cost_status=LlmCostStatus.ESTIMATED,
    )
    eur = llm.start(provider="mock", model="m", purpose="semantic_review", actor_id=actor_id)
    llm.finish(
        eur,
        status=LlmCallStatus.SUCCEEDED,
        outcome="accept",
        input_tokens=2,
        output_tokens=2,
        cost_amount=Decimal("0.50"),
        cost_currency="EUR",
        cost_status=LlmCostStatus.PROVIDER_REPORTED,
    )
    unknown = llm.start(provider="mock", model="m", purpose="semantic_review", actor_id=actor_id)
    llm.finish(unknown, status=LlmCallStatus.FAILED, outcome="failed")

    FeedbackRepository(db_session).create(
        actor_id=actor_id,
        feedback_type=FeedbackType.USABILITY.value,
        severity=FeedbackSeverity.HIGH.value,
        title="Pain",
        description="Recurring",
        request_id=uuid.uuid4(),
        trace_id=None,
        operation_log_id=None,
        proposal_id=None,
        entity_id=None,
        statement_id=None,
        source_id=None,
        context={"token": "secret"},
        fingerprint="fp-1",
    )
    open_row = FeedbackRepository(db_session).find_open_by_fingerprint("fp-1")
    assert open_row is not None
    FeedbackRepository(db_session).touch_occurrence(open_row)

    GovernanceRepository(db_session).create_proposal(
        proposed_by_actor_id=actor_id,
        proposal_type=ProposalType.CLASS.value,
        summary="Add Contract",
        payload={"key": "Contract"},
        request_id=uuid.uuid4(),
        status=ProposalStatus.SUBMITTED,
    )
    BatchRepository(db_session).create(
        actor_id=actor_id,
        request_id=uuid.uuid4(),
        item_count=3,
        status=BatchStatus.SUCCEEDED,
    )

    from semantic_memory.services.admin import AdminInspectionService

    summary = AdminInspectionService(db_session).summary()
    assert summary.operations.failed_by_operation.get("assert_statement", 0) >= 1
    assert summary.operations.rejected_by_error_code.get("UNKNOWN_PREDICATE", 0) >= 1
    assert Decimal(summary.llm_calls.cost_by_currency["USD"]) == Decimal("1.25")
    assert Decimal(summary.llm_calls.cost_by_currency["EUR"]) == Decimal("0.50")
    assert summary.llm_calls.known_cost_calls >= 2
    assert summary.llm_calls.unknown_cost_calls >= 1
    assert summary.feedback.total_open >= 1
    assert summary.feedback.repeated_open_feedback >= 1
    assert summary.feedback.by_severity.get("high", 0) >= 1
    assert summary.proposals.by_status.get("submitted", 0) >= 1
    assert summary.batches.by_status.get("succeeded", 0) >= 1


def test_conflict_entity_id_includes_object_side(db_session: Session) -> None:
    actor_id = _seed_actor(db_session, "conflict-actor")
    company_a = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="conflict-actor",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=f"Company A {uuid.uuid4()}",
            class_key="Organization",
        )
    )
    company_b = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="conflict-actor",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=f"Company B {uuid.uuid4()}",
            class_key="Organization",
        )
    )
    contract = EntityService(db_session).create_entity(
        CreateEntityRequest(
            actor_key="conflict-actor",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=f"Contract {uuid.uuid4()}",
            class_key="Document",
        )
    )
    assert company_a.entity is not None
    assert company_b.entity is not None
    assert contract.entity is not None
    provider = OntologyService(db_session).get_predicate(predicate_key="relatedTo")
    statements = StatementRepository(db_session)
    now = datetime.now(UTC)
    a = statements.create(
        subject_entity_id=contract.entity.id,
        predicate_id=provider.id,
        actor_id=actor_id,
        asserted_at=now,
        normalized_object=str(company_a.entity.id),
        object_entity_id=company_a.entity.id,
    )
    b = statements.create(
        subject_entity_id=contract.entity.id,
        predicate_id=provider.id,
        actor_id=actor_id,
        asserted_at=now,
        normalized_object=str(company_b.entity.id),
        object_entity_id=company_b.entity.id,
    )
    conflict = ConflictRepository(db_session).create(
        statement_a_id=a.id,
        statement_b_id=b.id,
        conflict_type="cardinality",
    )

    from semantic_memory.services.admin import AdminInspectionService

    listed = AdminInspectionService(db_session).list_conflicts(entity_id=company_a.entity.id)
    assert listed.total >= 1
    assert any(item.id == conflict.id for item in listed.items)


def test_admin_router_is_read_only() -> None:
    from semantic_memory.api import admin as admin_module

    flattened: set[str] = set()
    for route in admin_module.router.routes:
        methods = getattr(route, "methods", None)
        if methods:
            flattened.update(methods)
    assert flattened <= {"GET", "HEAD"}


def test_unknown_operation_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        f"/v1/admin/operations/{uuid.uuid4()}",
        headers=_admin_headers(),
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "UNKNOWN_OPERATION"
