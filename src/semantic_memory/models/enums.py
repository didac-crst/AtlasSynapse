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
