"""HTTP translation for typed domain errors."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from semantic_memory.exceptions import (
    AmbiguousEntityError,
    CardinalityViolationError,
    ConflictDetectedError,
    DbConstraintError,
    DomainError,
    DomainViolationError,
    DuplicateEntityError,
    DuplicateStatementError,
    IdempotencyKeyReusedError,
    InternalError,
    InvalidLiteralTypeError,
    InvalidStateTransitionError,
    OntologyCycleError,
    OntologyProposalRejectedError,
    OntologyReuseRecommendedError,
    RangeViolationError,
    RevisionConflictError,
    UnauthorizedOperationError,
    UnknownClassError,
    UnknownConflictError,
    UnknownEntityError,
    UnknownPredicateError,
    UnknownProposalError,
    UnknownSourceError,
    UnknownStatementError,
    ValidationFailedError,
)
from semantic_memory.schemas.errors import ErrorEnvelope

logger = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    UnknownClassError: 404,
    UnknownPredicateError: 404,
    UnknownEntityError: 404,
    UnknownSourceError: 404,
    UnknownStatementError: 404,
    UnknownConflictError: 404,
    UnknownProposalError: 404,
    AmbiguousEntityError: 409,
    DuplicateEntityError: 409,
    DuplicateStatementError: 409,
    ConflictDetectedError: 409,
    RevisionConflictError: 409,
    OntologyReuseRecommendedError: 409,
    OntologyProposalRejectedError: 422,
    OntologyCycleError: 422,
    DomainViolationError: 422,
    RangeViolationError: 422,
    CardinalityViolationError: 422,
    InvalidLiteralTypeError: 422,
    IdempotencyKeyReusedError: 409,
    UnauthorizedOperationError: 403,
    InvalidStateTransitionError: 409,
    ValidationFailedError: 422,
    DbConstraintError: 409,
    InternalError: 500,
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        status_code = _STATUS_BY_ERROR.get(type(exc), 400)
        body = ErrorEnvelope(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
            request_id=exc.request_id,
            retryable=exc.retryable,
        )
        return JSONResponse(status_code=status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = ErrorEnvelope(
            error_code=ValidationFailedError.error_code,
            message="Request validation failed",
            details={"errors": exc.errors()},
            retryable=False,
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(ValueError)
    async def _value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        logger.warning("Unhandled ValueError at transport boundary", exc_info=exc)
        body = ErrorEnvelope(
            error_code=ValidationFailedError.error_code,
            message="Request validation failed",
            details={},
            retryable=False,
        )
        return JSONResponse(status_code=422, content=body.model_dump())
