"""Agent feedback reporting, dedupe, redaction, and admin resolution."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.exceptions import UnauthorizedOperationError
from semantic_memory.mcp.server import MCPPlaceholder
from semantic_memory.mcp.tools import FeedbackMCPTools
from semantic_memory.models import ActorStatus, ActorType, FeedbackOutcome, FeedbackStatus
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES, Capability
from semantic_memory.repositories.actors import ActorRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.feedback import ReportFeedbackRequest, ResolveFeedbackRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.feedback import FeedbackService, compute_feedback_fingerprint


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": get_settings().admin_api_token}


def _envelope(actor_key: str) -> dict[str, object]:
    return {
        "actor_key": actor_key,
        "request_id": uuid.uuid4(),
        "idempotency_key": f"idem-{uuid.uuid4()}",
    }


def _ensure_reporter(session: Session, key: str = "feedback-reporter") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def _ensure_manager(session: Session, key: str = "feedback-manager") -> str:
    ActorRepository(session).create(
        key=key,
        actor_type=ActorType.SERVICE,
        status=ActorStatus.ACTIVE,
        capabilities=[
            Capability.FEEDBACK_READ.value,
            Capability.FEEDBACK_MANAGE.value,
        ],
    )
    return key


def test_fingerprint_is_stable_under_whitespace_and_case() -> None:
    left = compute_feedback_fingerprint(
        feedback_type="data_quality",
        title="  Missing Alias ",
        description="Entity needs\nan alias",
        entity_id=None,
        statement_id=None,
        source_id=None,
        proposal_id=None,
        operation_log_id=None,
    )
    right = compute_feedback_fingerprint(
        feedback_type="data_quality",
        title="missing alias",
        description="Entity needs an alias",
        entity_id=None,
        statement_id=None,
        source_id=None,
        proposal_id=None,
        operation_log_id=None,
    )
    assert left == right


def test_report_correlates_operation_log_for_reporting_actor_only(db_session: Session) -> None:
    from semantic_memory.models.enums import OperationStatus
    from semantic_memory.models.operations import OperationLog
    from semantic_memory.repositories.operations import OperationLogRepository

    reporter = _ensure_reporter(db_session, key="corr-reporter")
    other = _ensure_reporter(db_session, key="corr-other")
    reporter_actor = ActorService(db_session).find_by_key(reporter)
    other_actor = ActorService(db_session).find_by_key(other)
    assert reporter_actor is not None and other_actor is not None
    shared_request = uuid.uuid4()
    ops = OperationLogRepository(db_session)
    foreign = ops.start(
        actor_id=other_actor.id,
        operation_name="create_entity",
        request_id=shared_request,
        trace_id=uuid.uuid4(),
        idempotency_key="foreign",
        request_payload=None,
    )
    ops.finish(foreign, status=OperationStatus.SUCCESS)

    reported = FeedbackService(db_session).report_feedback(
        ReportFeedbackRequest.model_validate(
            {
                "actor_key": reporter,
                "request_id": shared_request,
                "idempotency_key": f"idem-{uuid.uuid4()}",
                "feedback_type": "warning",
                "severity": "low",
                "title": "Correlation check",
                "description": "Must bind to reporting actor operation only.",
            }
        )
    )
    assert reported.feedback.operation_log_id is not None
    assert reported.feedback.operation_log_id != foreign.id
    linked = db_session.get(OperationLog, reported.feedback.operation_log_id)
    assert linked is not None
    assert linked.actor_id == reporter_actor.id


def test_report_create_dedupe_and_redaction(db_session: Session) -> None:
    reporter = _ensure_reporter(db_session)
    service = FeedbackService(db_session, Settings(raw_payload_retention="redacted"))
    base = {
        **_envelope(reporter),
        "feedback_type": "suggestion",
        "severity": "medium",
        "title": "Neighborhood ranking surprise",
        "description": "Expected proximity signal to dominate but recency won.",
        "context": {"token": "secret-token", "note": "ok", "api_key": "abc"},
    }
    created = service.report_feedback(ReportFeedbackRequest.model_validate(base))
    assert created.outcome == FeedbackOutcome.CREATE
    assert created.feedback.status == FeedbackStatus.OPEN
    assert created.feedback.occurrence_count == 1
    assert created.feedback.fingerprint is not None
    assert created.feedback.context["token"] == "[REDACTED]"
    assert created.feedback.context["api_key"] == "[REDACTED]"
    assert created.feedback.context["note"] == "ok"
    assert created.feedback.operation_log_id is not None

    again = service.report_feedback(
        ReportFeedbackRequest.model_validate(
            {
                **_envelope(reporter),
                "feedback_type": "suggestion",
                "severity": "medium",
                "title": "Neighborhood ranking surprise",
                "description": "Expected proximity signal to dominate but recency won.",
                "context": {"token": "other"},
            }
        )
    )
    assert again.outcome == FeedbackOutcome.DEDUPED
    assert again.feedback.id == created.feedback.id
    assert again.feedback.occurrence_count == 2


def test_resolve_requires_manage_capability(db_session: Session) -> None:
    reporter = _ensure_reporter(db_session)
    manager = _ensure_manager(db_session)
    service = FeedbackService(db_session)
    reported = service.report_feedback(
        ReportFeedbackRequest.model_validate(
            {
                **_envelope(reporter),
                "feedback_type": "usability",
                "severity": "low",
                "title": "Tool description unclear",
                "description": "report_feedback payload fields are hard to discover.",
            }
        )
    )
    with pytest.raises(UnauthorizedOperationError):
        service.resolve_feedback(
            ResolveFeedbackRequest.model_validate(
                {
                    **_envelope(reporter),
                    "feedback_id": reported.feedback.id,
                    "status": "resolved",
                    "resolution": "docs updated",
                }
            )
        )
    resolved = service.resolve_feedback(
        ResolveFeedbackRequest.model_validate(
            {
                **_envelope(manager),
                "feedback_id": reported.feedback.id,
                "status": "resolved",
                "resolution": "docs updated",
            }
        )
    )
    assert resolved.feedback.status == FeedbackStatus.RESOLVED
    assert resolved.feedback.resolution == "docs updated"
    assert resolved.feedback.resolved_by_actor_id is not None


def test_http_report_list_and_resolve(client: TestClient) -> None:
    ensure = client.post(
        "/v1/actors/ensure",
        headers=_admin_headers(),
        json={
            "key": "http-feedback",
            "actor_type": "agent",
            "capabilities": [cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
            "status": "active",
        },
    )
    assert ensure.status_code == 200

    reported = client.post(
        "/v1/feedback",
        json={
            "actor_key": "http-feedback",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "feedback_type": "ontology_gap",
            "severity": "high",
            "title": "Missing Document subtype",
            "description": "Need a Contract class under Document.",
            "context": {"password": "nope"},
        },
    )
    assert reported.status_code == 200
    body = reported.json()
    assert body["outcome"] == "CREATE"
    feedback_id = body["feedback"]["id"]
    assert body["feedback"]["context"]["password"] == "[REDACTED]"

    listed = client.get(
        "/v1/feedback",
        params={"actor_key": "system", "status": "open"},
    )
    assert listed.status_code == 200
    assert any(item["id"] == feedback_id for item in listed.json()["items"])

    fetched = client.get(
        f"/v1/feedback/{feedback_id}",
        params={"actor_key": "system"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Missing Document subtype"

    resolved = client.post(
        "/v1/feedback/resolve",
        json={
            "actor_key": "system",
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "feedback_id": feedback_id,
            "status": "acknowledged",
            "resolution": "tracked for ontology proposal",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["feedback"]["status"] == "acknowledged"


def test_mcp_report_feedback_and_tool_registration(db_session: Session) -> None:
    reporter = _ensure_reporter(db_session, key="mcp-feedback")
    env = _envelope(reporter)
    tools = FeedbackMCPTools(db_session)
    result = tools.report_feedback(
        {
            "actor_key": reporter,
            "request_id": str(env["request_id"]),
            "idempotency_key": env["idempotency_key"],
            "feedback_type": "performance",
            "severity": "info",
            "title": "Search latency",
            "description": "search_entities felt slow on large graphs.",
        }
    )
    assert "error_code" not in result
    assert result["outcome"] == "CREATE"
    # Default surface is agent; feedback tools live on advanced/all.
    assert "report_feedback" in MCPPlaceholder.from_settings(Settings(mcp_tool_surface="all")).tools
