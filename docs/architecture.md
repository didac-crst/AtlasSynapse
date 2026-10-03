# AtlasSynapse architecture

## Purpose

AtlasSynapse is a self-hosted semantic memory service for AI agents. It stores entities, ontology classes, predicates, statements, temporal validity, provenance, evidence, conflicts, ontology evolution, and audit records.

The database is a stable physical substrate. Semantic concepts are data in the ontology tables; new concepts must not create SQL tables or execute DDL through MCP.

## Technology baseline

The implementation baseline is Python 3.12 or newer with PostgreSQL, SQLAlchemy, Alembic, Pydantic, and FastAPI-style service boundaries. The MCP adapter remains thin and typed. LLM/provider integrations are replaceable dependencies, and `pgvector` is optional because core correctness and operation do not depend on embeddings.

This choice is deliberate: Python provides strong MCP and provider integration, PostgreSQL provides mature transactional and JSONB support, Pydantic provides strict request/response schemas, and SQLAlchemy/Alembic provide explicit persistence and migration boundaries.

## Logical planes

### Knowledge plane

The knowledge plane handles high-volume deterministic operations:

- entity creation and reuse;
- entity typing and aliases;
- statement assertion and duplicate detection;
- evidence and sources;
- temporal history;
- supersession and retraction;
- explicit entity merges;
- search and neighborhood retrieval.

Routine knowledge writes do not require an LLM.

### Ontology control plane

The control plane handles low-volume governed operations:

- classes, predicates, constraints, aliases, and inheritance;
- deterministic structural, cycle, domain, range, and revision validation;
- optional external semantic review;
- transactional application of accepted changes.

External agents create proposals. Direct ontology application belongs to a governed controller/service capability.

## Boundary rules

- MCP translates typed requests and responses; it does not contain business logic.
- Services implement semantic rules and transaction boundaries.
- Repositories perform persistence and query composition.
- ORM models describe persistence and avoid business logic.
- Validation is explicit and reusable.
- Domain exceptions are translated into stable transport errors.
- Every mutation is attributable to an actor and request ID.
- Every mutating operation requires an idempotency key.

## Target package layout

```text
src/semantic_memory/
├── config.py
├── db.py
├── models/
├── schemas/
├── repositories/
├── services/
├── validation/
├── mcp/
├── api/
└── observability/
```

The complete planned layout is recorded in [cursor-implementation.md](cursor-implementation.md).

## Data domains

The physical schema is divided into Ontology, Knowledge, Provenance, Governance, Operations, and Reasoning domains. The table-level contract is recorded in [ontology-model.md](ontology-model.md).

## Transaction principles

Write services define explicit transaction boundaries for resolution plus creation, duplicate detection plus assertion, supersession, entity merge, ontology application, and idempotency processing. Idempotency reservation and operation execution must be coordinated so retries cannot double-write.

## Readiness

`/health/live` checks process liveness. `/health/ready` checks database connectivity and migration compatibility. External semantic review availability is not a knowledge-plane readiness dependency.

## Explicit non-goals for v1

No RDF store, OWL reasoner, SPARQL endpoint, autonomous entity merging, probabilistic truth engine, UI, graph database migration, required vector dependency, dynamic SQL schema creation, distributed event bus, or multi-node clustering.
