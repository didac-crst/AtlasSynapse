"""Typed domain exceptions translated at transport boundaries."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base domain error with a stable transport error code."""

    error_code: str = "INTERNAL_ERROR"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.request_id = request_id


class UnknownClassError(DomainError):
    error_code = "UNKNOWN_CLASS"


class UnknownEntityError(DomainError):
    error_code = "UNKNOWN_ENTITY"


class AmbiguousEntityError(DomainError):
    error_code = "AMBIGUOUS_ENTITY"


class DuplicateEntityError(DomainError):
    error_code = "DUPLICATE_ENTITY"


class IdempotencyKeyReusedError(DomainError):
    error_code = "IDEMPOTENCY_KEY_REUSED"


class UnauthorizedOperationError(DomainError):
    error_code = "UNAUTHORIZED_OPERATION"


class InvalidStateTransitionError(DomainError):
    error_code = "INVALID_STATE_TRANSITION"


class DbConstraintError(DomainError):
    error_code = "DB_CONSTRAINT_ERROR"


class InternalError(DomainError):
    error_code = "INTERNAL_ERROR"
    retryable = True
