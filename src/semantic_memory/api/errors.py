"""HTTP translation for typed domain errors."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from semantic_memory.exceptions import (
    AmbiguousEntityError,
    DbConstraintError,
    DomainError,
    DuplicateEntityError,
    IdempotencyKeyReusedError,
    InternalError,
    InvalidStateTransitionError,
    UnauthorizedOperationError,
    UnknownClassError,
    UnknownEntityError,
)
from semantic_memory.schemas.errors import ErrorEnvelope

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    UnknownClassError: 404,
    UnknownEntityError: 404,
    AmbiguousEntityError: 409,
    DuplicateEntityError: 409,
    IdempotencyKeyReusedError: 409,
    UnauthorizedOperationError: 403,
    InvalidStateTransitionError: 409,
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
