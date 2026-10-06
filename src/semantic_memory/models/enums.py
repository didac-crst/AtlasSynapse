"""Application string enums mirrored by PostgreSQL CHECK constraints."""

from enum import StrEnum


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SERVICE = "service"
    SYSTEM = "system"
    ADMIN = "admin"


class ActorStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    MERGED = "merged"


class StatementStatus(StrEnum):
    ASSERTED = "asserted"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class ValueKind(StrEnum):
    ENTITY = "entity"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    JSON = "json"


class Cardinality(StrEnum):
    ONE = "one"
    MANY = "many"


class AliasTargetType(StrEnum):
    CLASS = "class"
    PREDICATE = "predicate"


class AliasIdentityStrength(StrEnum):
    """How strongly an entity alias establishes identity.

    supporting: observed/inferred lexical hint; never alone decisive for MATCH.
    authoritative: confirmed via merge, user confirmation, or authoritative ID.
    """

    SUPPORTING = "supporting"
    AUTHORITATIVE = "authoritative"


class ConstraintType(StrEnum):
    DOMAIN = "domain"
    RANGE = "range"
    CARDINALITY = "cardinality"
    CUSTOM = "custom"


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class GateDecision(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    MANUAL_REVIEW = "manual_review"
    REUSE_RECOMMENDED = "reuse_recommended"


class OntologyChangeObjectType(StrEnum):
    CLASS = "class"
    PREDICATE = "predicate"
    CONSTRAINT = "constraint"
    ALIAS = "alias"
    CLASS_PARENT = "class_parent"


class ProposalType(StrEnum):
    CLASS = "class"
    PREDICATE = "predicate"
    CONSTRAINT = "constraint"
    ALIAS = "alias"
    CLASS_PARENT = "class_parent"


class EmbeddingObjectType(StrEnum):
    CLASS = "class"
    PREDICATE = "predicate"
    ENTITY = "entity"


class LlmCallStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    MANUAL_REVIEW = "manual_review"


class LlmCostStatus(StrEnum):
    ESTIMATED = "estimated"
    PROVIDER_REPORTED = "provider_reported"
    UNKNOWN = "unknown"


class OperationStatus(StrEnum):
    STARTED = "started"
    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"


class BatchStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class FeedbackType(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    DATA_QUALITY = "data_quality"
    ONTOLOGY_GAP = "ontology_gap"
    AMBIGUITY = "ambiguity"
    SUGGESTION = "suggestion"
    USABILITY = "usability"
    PERFORMANCE = "performance"
    SECURITY = "security"
    DOCUMENTATION = "documentation"
    UNEXPECTED_BEHAVIOR = "unexpected_behavior"
    MISSING_CAPABILITY = "missing_capability"


class FeedbackSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FeedbackStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class FeedbackOutcome(StrEnum):
    CREATE = "CREATE"
    DEDUPED = "DEDUPED"


class SemanticReviewStage(StrEnum):
    INITIAL = "initial"
    CHALLENGE = "challenge"
    CLARIFICATION = "clarification"


class SemanticChallengeStatus(StrEnum):
    ACCEPTED_FOR_REVIEW = "accepted_for_review"
    REJECTED_AS_INSUBSTANTIVE = "rejected_as_insubstantive"
    REVIEWED = "reviewed"


class SemanticClarificationStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    SUPERSEDED = "superseded"
    RESOLVED = "resolved"


class WriteClarificationStatus(StrEnum):
    """Control-plane status for identity/write clarification handles."""

    OPEN = "open"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class WriteClarificationResolution(StrEnum):
    """Structured answer to an identity/write clarification."""

    CHOSEN_ENTITY = "chosen_entity"
    CREATE_NEW = "create_new"
    REJECT = "reject"
