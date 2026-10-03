"""LLM call observability tests."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from semantic_memory.config import Settings
from semantic_memory.models import ActorType, LlmCallStatus, LlmCostStatus
from semantic_memory.models.capabilities import DEFAULT_AGENT_CAPABILITIES
from semantic_memory.models.enums import ProposalType
from semantic_memory.repositories.llm_calls import LlmCallLogRepository
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.proposals import ProposeClassRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.llm_logging import (
    DatabaseLlmCallLogger,
    LlmCallCompletion,
    LlmCallContext,
)
from semantic_memory.services.llm_pricing import estimate_cost
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.review import (
    FailingSemanticReviewer,
    LoggingSemanticReviewer,
    MockSemanticReviewer,
    ReviewDecision,
    ReviewRequest,
)


def _ensure_actor(session: Session, key: str = "llm-actor") -> str:
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=key,
            actor_type=ActorType.AGENT,
            capabilities=[cap.value for cap in DEFAULT_AGENT_CAPABILITIES],
        )
    )
    return key


def test_estimate_cost_from_tokens_and_pricing_snapshot() -> None:
    cost = estimate_cost(
        provider="mock",
        model="mock-reviewer",
        input_tokens=1000,
        output_tokens=500,
    )
    assert cost.status == LlmCostStatus.ESTIMATED
    assert cost.amount == Decimal("0.002")
    assert cost.currency == "USD"
    assert cost.pricing_version == "atlas-mock-pricing-2026-10"
    assert cost.pricing_snapshot is not None
    assert cost.pricing_snapshot["input_rate_per_token"] == "0.000001"


def test_unknown_cost_when_rates_missing() -> None:
    cost = estimate_cost(
        provider="unknown-provider",
        model="unknown-model",
        input_tokens=10,
        output_tokens=10,
    )
    assert cost.status == LlmCostStatus.UNKNOWN
    assert cost.amount is None


def test_provider_reported_cost() -> None:
    cost = estimate_cost(
        provider="mock",
        model="mock-reviewer",
        input_tokens=1,
        output_tokens=1,
        provider_reported_cost=Decimal("1.25"),
        provider_reported_currency="EUR",
    )
    assert cost.status == LlmCostStatus.PROVIDER_REPORTED
    assert cost.amount == Decimal("1.25")
    assert cost.currency == "EUR"


def test_successful_logged_call_with_tokens_and_duration(db_session: Session) -> None:
    logger = DatabaseLlmCallLogger(db_session, Settings(raw_payload_retention="redacted"))
    started = logger.begin(
        LlmCallContext(
            purpose="semantic_review",
            provider="mock",
            model="mock-reviewer",
            request_id=uuid.uuid4(),
            metadata={"prompt": "secret-should-not-matter", "excerpt": "private text"},
        )
    )
    assert started.status == LlmCallStatus.STARTED.value
    finished = logger.complete(
        started,
        LlmCallCompletion(
            status=LlmCallStatus.SUCCEEDED,
            outcome="accept",
            input_tokens=40,
            output_tokens=10,
            metadata={"excerpt": "still private", "token": "abc"},
        ),
    )
    assert finished.status == LlmCallStatus.SUCCEEDED.value
    assert finished.duration_ms is not None and finished.duration_ms >= 0
    assert finished.completed_at is not None
    assert finished.total_tokens == 50
    assert finished.cost_status == LlmCostStatus.ESTIMATED.value
    assert finished.cost_amount is not None
    assert finished.pricing_snapshot is not None
    assert finished.metadata_json.get("excerpt") == "[REDACTED]"
    assert finished.metadata_json.get("token") == "[REDACTED]"


def test_failed_and_unavailable_calls(db_session: Session) -> None:
    logger = DatabaseLlmCallLogger(db_session)
    failed = logger.begin(
        LlmCallContext(purpose="semantic_review", provider="mock", model="mock-reviewer")
    )
    logger.complete(
        failed,
        LlmCallCompletion(
            status=LlmCallStatus.FAILED,
            outcome="failed",
            error_code="INTERNAL_ERROR",
            error_message="boom",
        ),
    )
    assert failed.status == LlmCallStatus.FAILED.value
    assert failed.error_code == "INTERNAL_ERROR"

    unavailable = logger.begin(
        LlmCallContext(purpose="semantic_review", provider="mock", model="failing-reviewer")
    )
    logger.complete(
        unavailable,
        LlmCallCompletion(
            status=LlmCallStatus.UNAVAILABLE,
            outcome="unavailable",
            error_code="DEPENDENCY_UNAVAILABLE",
            error_message="down",
        ),
    )
    assert unavailable.status == LlmCallStatus.UNAVAILABLE.value


def test_reviewer_logs_and_links_to_operation_request(db_session: Session) -> None:
    actor_key = _ensure_actor(db_session)
    service = ProposalService(
        db_session,
        settings=Settings(semantic_review_mode="mock"),
        reviewer=MockSemanticReviewer(),
    )
    request_id = uuid.uuid4()
    result = service.propose_class(
        ProposeClassRequest(
            actor_key=actor_key,
            request_id=request_id,
            idempotency_key=f"llm-{uuid.uuid4()}",
            key="LoggedLab",
            parent_keys=["Organization"],
            metadata={"review_decision": ReviewDecision.ACCEPT.value},
        )
    )
    assert result.proposal.id is not None
    calls = LlmCallLogRepository(db_session).list_for_request(request_id)
    assert len(calls) == 1
    call = calls[0]
    assert call.purpose == "semantic_review"
    assert call.provider == "mock"
    assert call.request_id == request_id
    assert call.operation_log_id is not None
    assert call.actor_id is not None
    assert call.status == LlmCallStatus.SUCCEEDED.value
    assert call.outcome == ReviewDecision.ACCEPT.value
    assert call.input_tokens is not None and call.input_tokens > 0
    assert call.cost_status == LlmCostStatus.ESTIMATED.value
    assert "payload" not in (call.metadata_json or {})


def test_unavailable_reviewer_logs_unavailable(db_session: Session) -> None:
    logger = DatabaseLlmCallLogger(db_session)
    reviewer = LoggingSemanticReviewer(FailingSemanticReviewer(), logger)
    try:
        reviewer.review(
            ReviewRequest(
                proposal_type=ProposalType.CLASS,
                payload={"namespace_key": "core", "key": "X"},
                request_id=uuid.uuid4(),
            )
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected reviewer failure")
    # Latest row should be unavailable.
    from sqlalchemy import select

    from semantic_memory.models.llm_calls import LlmCallLog

    row = db_session.scalar(select(LlmCallLog).order_by(LlmCallLog.started_at.desc()))
    assert row is not None
    assert row.status == LlmCallStatus.UNAVAILABLE.value


def test_none_retention_strips_metadata(db_session: Session) -> None:
    logger = DatabaseLlmCallLogger(db_session, Settings(raw_payload_retention="none"))
    row = logger.begin(
        LlmCallContext(
            purpose="semantic_review",
            provider="mock",
            model="mock-reviewer",
            metadata={"excerpt": "secret text", "ok": True},
        )
    )
    assert row.metadata_json == {}
