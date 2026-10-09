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


class KnowledgeIngestionMode(StrEnum):
    """Whether an ingestion run may persist commits (execute) or only preview (dry_run)."""

    EXECUTE = "execute"
    DRY_RUN = "dry_run"


class KnowledgeIngestionStatus(StrEnum):
    """Operational run status for governed knowledge ingestion."""

    ACCEPTED = "accepted"
    EXTRACTING = "extracting"
    RESOLVING = "resolving"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    PAUSED = "paused"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"


class KnowledgeIngestionPauseReason(StrEnum):
    """Operational pause reasons (not semantic decisions)."""

    BUDGET_EXHAUSTED = "budget_exhausted"


class KnowledgeCandidateKind(StrEnum):
    ASSERTION = "assertion"
    HYPOTHESIS = "hypothesis"
    RECOMMENDATION = "recommendation"
    QUESTION = "question"


class KnowledgeCandidatePolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class KnowledgeCandidateDerivation(StrEnum):
    EXPLICIT = "explicit"
    NORMALIZED = "normalized"
    INFERRED = "inferred"


class KnowledgeCandidateEpistemicStatus(StrEnum):
    """Semantic lifecycle — orthogonal to processing state."""

    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    OPEN = "open"
    ANSWERED = "answered"


class KnowledgeCandidateState(StrEnum):
    """Processing state machine for a candidate within an ingestion run."""

    EXTRACTED = "extracted"
    ACTIONABLE = "actionable"
    BLOCKED = "blocked"
    RESOLVED_COMMIT_ELIGIBLE = "resolved_commit_eligible"
    COMMITTED = "committed"
    DISCARDED = "discarded"
    FAILED = "failed"


class KnowledgeCandidateDependencyKind(StrEnum):
    """Why child waits on parent (parent is the prerequisite)."""

    REQUIRES_RESOLUTION = "requires_resolution"
    REQUIRES_COMMIT = "requires_commit"
    SAME_SUBJECT = "same_subject"
    GENERIC = "generic"


class KnowledgeIngestionClarificationKind(StrEnum):
    IDENTITY = "identity"
    ONTOLOGY = "ontology"
    PACKAGE_LOCAL = "package_local"
    POLICY = "policy"


class KnowledgeIngestionClarificationStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class KnowledgeIngestionEffectType(StrEnum):
    CREATE_ENTITY = "create_entity"
    ASSERT_STATEMENT = "assert_statement"
    ADD_EVIDENCE = "add_evidence"


class KnowledgeIngestionEffectStatus(StrEnum):
    APPLIED = "applied"
    FAILED = "failed"


class MemoryQualityIssueType(StrEnum):
    """Residual post-write quality issue types (not conflicts)."""

    SUPERSESSION_INTEGRITY = "supersession_integrity"
    POSSIBLE_DUPLICATE_ENTITY = "possible_duplicate_entity"
    WEAK_OR_MISSING_PROVENANCE = "weak_or_missing_provenance"


class MemoryQualityIssueStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class MemoryQualitySeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MemoryQualityResolution(StrEnum):
    """Terminal resolutions; null while status=open."""

    AUTO_RESOLVED = "auto_resolved"
    CORRECTED = "corrected"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"
    STRUCTURAL_REPAIR = "structural_repair"
    CONFIRMED_SAME = "confirmed_same"
    CONFIRMED_DIFFERENT = "confirmed_different"
    UNKNOWN = "unknown"
    NO_ACTION = "no_action"


class SupersessionRepairOutcome(StrEnum):
    """Outcomes for repair_supersession_integrity."""

    REPAIRED = "REPAIRED"
    WOULD_REPAIR = "WOULD_REPAIR"
    NO_LONGER_APPLICABLE = "NO_LONGER_APPLICABLE"


class QualityRouteOutcome(StrEnum):
    """Post-write quality router outcomes (no REQUEST_CLARIFICATION)."""

    IGNORE = "ignore"
    OPEN_QUALITY_ISSUE = "open_quality_issue"
    UPSERT_CONFLICT = "upsert_conflict"
    IDENTITY_REVIEW = "identity_review"
    AUTO_RESOLVE = "auto_resolve"
