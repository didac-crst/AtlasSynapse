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


class UnknownPredicateError(DomainError):
    error_code = "UNKNOWN_PREDICATE"


class UnknownEntityError(DomainError):
    error_code = "UNKNOWN_ENTITY"


class UnknownStatementError(DomainError):
    error_code = "UNKNOWN_STATEMENT"


class UnknownSourceError(DomainError):
    error_code = "UNKNOWN_SOURCE"


class AmbiguousSourceError(DomainError):
    error_code = "AMBIGUOUS_SOURCE"


class AmbiguousEntityError(DomainError):
    error_code = "AMBIGUOUS_ENTITY"


class DuplicateEntityError(DomainError):
    error_code = "DUPLICATE_ENTITY"


class DuplicateStatementError(DomainError):
    error_code = "DUPLICATE_STATEMENT"


class DomainViolationError(DomainError):
    error_code = "DOMAIN_VIOLATION"


class RangeViolationError(DomainError):
    error_code = "RANGE_VIOLATION"


class CardinalityViolationError(DomainError):
    error_code = "CARDINALITY_VIOLATION"


class ConflictDetectedError(DomainError):
    error_code = "CONFLICT_DETECTED"


class UnknownConflictError(DomainError):
    error_code = "UNKNOWN_CONFLICT"


class UnknownMemoryQualityIssueError(DomainError):
    error_code = "UNKNOWN_MEMORY_QUALITY_ISSUE"


class OntologyCycleError(DomainError):
    error_code = "ONTOLOGY_CYCLE"


class RevisionConflictError(DomainError):
    error_code = "REVISION_CONFLICT"


class OntologyProposalRejectedError(DomainError):
    error_code = "ONTOLOGY_PROPOSAL_REJECTED"


class OntologyReuseRecommendedError(DomainError):
    error_code = "ONTOLOGY_REUSE_RECOMMENDED"


class UnknownProposalError(DomainError):
    error_code = "UNKNOWN_PROPOSAL"


class ClarificationRequestNotFoundError(DomainError):
    error_code = "CLARIFICATION_REQUEST_NOT_FOUND"


class ClarificationRequestAlreadyResolvedError(DomainError):
    error_code = "CLARIFICATION_REQUEST_ALREADY_RESOLVED"


class ClarificationRequestSupersededError(DomainError):
    error_code = "CLARIFICATION_REQUEST_SUPERSEDED"


class UnknownFeedbackError(DomainError):
    error_code = "UNKNOWN_FEEDBACK"


class UnknownOperationError(DomainError):
    error_code = "UNKNOWN_OPERATION"


class UnknownLlmCallError(DomainError):
    error_code = "UNKNOWN_LLM_CALL"


class UnknownBatchError(DomainError):
    error_code = "UNKNOWN_BATCH"


class UnknownKnowledgeIngestionError(DomainError):
    error_code = "UNKNOWN_KNOWLEDGE_INGESTION"


class UnknownKnowledgeCandidateError(DomainError):
    error_code = "UNKNOWN_KNOWLEDGE_CANDIDATE"


class InvalidLiteralTypeError(DomainError):
    error_code = "INVALID_LITERAL_TYPE"


class IdempotencyKeyReusedError(DomainError):
    error_code = "IDEMPOTENCY_KEY_REUSED"


class UnauthorizedOperationError(DomainError):
    error_code = "UNAUTHORIZED_OPERATION"


class InvalidStateTransitionError(DomainError):
    error_code = "INVALID_STATE_TRANSITION"


class ValidationFailedError(DomainError):
    error_code = "VALIDATION_FAILED"


class UnsupportedSourceFormatError(DomainError):
    error_code = "UNSUPPORTED_SOURCE_FORMAT"


class KnowledgeExtractionConflictError(DomainError):
    """Same candidate_key with incompatible staged payload on re-extraction."""

    error_code = "KNOWLEDGE_EXTRACTION_CONFLICT"


class OntologySeedConflictError(DomainError):
    """Existing ontology key conflicts with required seed/extension semantics."""

    error_code = "ONTOLOGY_SEED_CONFLICT"


class DbConstraintError(DomainError):
    error_code = "DB_CONSTRAINT_ERROR"


class InternalError(DomainError):
    error_code = "INTERNAL_ERROR"
    retryable = True
