# Error catalog

Stable transport error codes:

```text
UNKNOWN_CLASS
UNKNOWN_PREDICATE
UNKNOWN_ENTITY
UNKNOWN_STATEMENT
DOMAIN_VIOLATION
RANGE_VIOLATION
CARDINALITY_VIOLATION
INVALID_LITERAL_TYPE
ONTOLOGY_CYCLE
DUPLICATE_ENTITY
AMBIGUOUS_ENTITY
DUPLICATE_STATEMENT
CONFLICT_DETECTED
REVISION_CONFLICT
ONTOLOGY_PROPOSAL_REJECTED
ONTOLOGY_REUSE_RECOMMENDED
IDEMPOTENCY_KEY_REUSED
UNAUTHORIZED_OPERATION
INVALID_STATE_TRANSITION
DB_CONSTRAINT_ERROR
DEPENDENCY_UNAVAILABLE
INTERNAL_ERROR
```

Errors must be raised as typed domain exceptions and translated at API/MCP boundaries. Raw SQLAlchemy or PostgreSQL exceptions must not be exposed.

`retryable` describes whether a caller may retry the same request after the condition is addressed. Deterministic validation rejections are generally non-retryable; transient infrastructure failures may be retryable.
