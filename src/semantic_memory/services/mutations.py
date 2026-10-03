"""Idempotent mutation runner with operation audit logging."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.exceptions import (
    DbConstraintError,
    DomainError,
    IdempotencyKeyReusedError,
    InternalError,
)
from semantic_memory.models import Actor
from semantic_memory.models.enums import OperationStatus
from semantic_memory.repositories.idempotency import IdempotencyRepository, hash_request_payload
from semantic_memory.repositories.operations import OperationLogRepository
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.services.redaction import prepare_audit_payload


class MutationRunner:
    """Coordinate idempotency reservation, execution, and operation logging."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._idempotency = IdempotencyRepository(session)
        self._operations = OperationLogRepository(session)

    def run[T: BaseModel](
        self,
        *,
        actor: Actor,
        operation_name: str,
        request: MutationEnvelope,
        response_model: type[T],
        execute: Callable[[], T],
        constraint_name: str,
    ) -> T:
        payload = request.model_dump(
            mode="json",
            exclude={"request_id", "trace_id", "idempotency_key"},
        )
        request_hash = hash_request_payload(payload)
        audit_request = prepare_audit_payload(payload, mode=self._settings.raw_payload_retention)
        operation = self._operations.start(
            actor_id=actor.id,
            operation_name=operation_name,
            request_id=request.request_id,
            trace_id=request.trace_id,
            idempotency_key=request.idempotency_key,
            request_payload=audit_request,
        )

        try:
            with self._session.begin_nested():
                result = self._execute_with_idempotency(
                    actor_id=actor.id,
                    operation_name=operation_name,
                    request=request,
                    request_hash=request_hash,
                    response_model=response_model,
                    execute=execute,
                    operation_log_id=operation.id,
                )
            response_payload = prepare_audit_payload(
                result.model_dump(mode="json"),
                mode=self._settings.raw_payload_retention,
            )
            self._operations.finish(
                operation,
                status=OperationStatus.SUCCESS,
                response_payload=response_payload,
            )
            return result
        except DomainError as exc:
            self._operations.finish(
                operation,
                status=OperationStatus.REJECTED,
                error_code=exc.error_code,
                error_message=exc.message,
            )
            if exc.request_id is None:
                exc.request_id = str(request.request_id)
            raise
        except IntegrityError as exc:
            error = DbConstraintError(
                f"{operation_name} violated a database constraint",
                details={"constraint": constraint_name},
                request_id=str(request.request_id),
            )
            self._operations.finish(
                operation,
                status=OperationStatus.FAILED,
                error_code=error.error_code,
                error_message=error.message,
            )
            raise error from exc
        except Exception:
            self._operations.finish(
                operation,
                status=OperationStatus.FAILED,
                error_code=InternalError.error_code,
                error_message="Unexpected mutation failure",
            )
            raise

    def _execute_with_idempotency[T: BaseModel](
        self,
        *,
        actor_id: uuid.UUID,
        operation_name: str,
        request: MutationEnvelope,
        request_hash: str,
        response_model: type[T],
        execute: Callable[[], T],
        operation_log_id: uuid.UUID,
    ) -> T:
        reservation, created = self._idempotency.reserve_or_get(
            actor_id=actor_id,
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
            operation_name=operation_name,
        )
        reservation.operation_log_id = operation_log_id
        self._session.flush()

        if reservation.request_hash != request_hash:
            raise IdempotencyKeyReusedError(
                "Idempotency key was reused with a different payload",
                details={
                    "idempotency_key": request.idempotency_key,
                    "actor_key": request.actor_key,
                },
                request_id=str(request.request_id),
            )
        if reservation.response_payload is not None:
            return response_model.model_validate(reservation.response_payload)
        if not created:
            raise InternalError(
                "Idempotency key is reserved by an in-flight request",
                details={"idempotency_key": request.idempotency_key},
                request_id=str(request.request_id),
            )

        result = execute()
        self._idempotency.store_response(reservation, result.model_dump(mode="json"))
        return result
