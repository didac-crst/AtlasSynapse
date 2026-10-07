# AtlasSynapse architecture

## Purpose

AtlasSynapse is a self-hosted **governed semantic-memory layer** for AI agents.

Agents accumulate open-ended structured knowledge (entities, relationships, time,
evidence). When the existing semantic model is insufficient, they may participate
in evolving that model — but AtlasSynapse independently governs identity, reuse,
structural validity, semantic review, clarification, and application.

The database is a stable physical substrate. Semantic concepts are data in the
ontology tables; new concepts must not create SQL tables or execute DDL through MCP.

For the architectural argument, see [design-thesis.md](design-thesis.md).

## Technology baseline

Python 3.12+, PostgreSQL, SQLAlchemy, Alembic, Pydantic, and FastAPI-style service
boundaries. The MCP adapter remains thin and typed. LLM/provider integrations are
replaceable. `pgvector` is optional; core correctness does not depend on embeddings.

## Logical planes

### Knowledge plane

High-volume deterministic operations:

- entity creation and reuse;
- server-owned identity resolution on writes (MATCH / CREATE / CLARIFY);
- statement assertion, correction, supersession, and retraction;
- evidence and sources;
- temporal history;
- explicit entity merges;
- hybrid retrieval (lexical, temporal, predicate intent, bounded anchors).

Routine knowledge writes do not require an LLM.

### Ontology control plane

Low-volume governed operations:

- classes, predicates, constraints, aliases, and inheritance;
- proposals with deterministic structural, cycle, domain, range, and revision gates;
- optional semantic reuse / overlap review;
- clarification and challenge;
- authorized apply with live revalidation (`ontology.apply`).

External agents create proposals. Applying an ontology change is a separate,
capability-gated commit — proposal ≠ mutation.

### Clarification as operational state

Identity (and some ontology) ambiguity creates durable **control-plane** handles
(`write_clarification_request` and ontology clarification requests).

**Clarification state is operational/control-plane state, not knowledge and not
provenance.** It stores a frozen mutation (or review question), candidates, expiry,
and one-shot resolution metadata so a later answer can resume safely after
rechecking current production state.

### Full-path dry-run

`dry_run=true` executes the same identity, validation, and semantic-review path
inside a database savepoint and rolls knowledge mutations back. It is not a
separate approximate validator. Clarification handles created from dry-run remain
dry-run on answer; production persistence requires an explicit execute request.
See [operations.md](operations.md) and [invariants.md](invariants.md).

## Two feedback loops

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"13px","lineColor":"#64748B"}}}%%
flowchart TB
  subgraph knowledge [Knowledge accumulation]
    direction TD
    k1[Information] --> k2[Identity resolution]
    k2 --> k3[Entities + statements]
    k3 --> k4[Evidence + temporal state]
    k4 --> k5[Retrieval]
    k5 --> k6[Reasoning]
  end

  subgraph model [Semantic-model evolution]
    direction TD
    m1[New knowledge] --> m2{Ontology sufficient?}
    m2 -->|yes| m3[Reuse semantics]
    m2 -->|no| m4[Propose]
    m4 --> m5[Gates + semantic review]
    m5 --> m6[Clarify if needed]
    m6 --> m7[Authorized apply]
  end

  classDef k fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:1.5px
  classDef m fill:#EEF2F6,stroke:#5B6B7C,color:#1F2933,stroke-width:1.5px
  classDef decision fill:#F7F8FA,stroke:#94A3B8,color:#1F2933,stroke-width:1.5px
  classDef ok fill:#E8F7EF,stroke:#2F8F5B,color:#145C32,stroke-width:1.5px
  class k1,k2,k3,k4,k5,k6 k
  class m1,m4,m5,m6,m7 m
  class m2 decision
  class m3 ok
```

MCP tool surfaces (`agent` / `advanced` / `admin` / `all`) are **visibility UX**,
not authorization. Capability checks, review gates, idempotency, and audit still
apply. See [mcp-agent-surface.md](mcp-agent-surface.md).

## Boundary rules

- MCP translates typed requests and responses; it does not contain business logic.
- Services implement semantic rules and transaction boundaries.
- Repositories perform persistence and query composition.
- ORM models describe persistence and avoid business logic.
- Validation is explicit and reusable.
- Domain exceptions are translated into stable transport errors.
- Every mutation is attributable to an actor and request ID.
- Every mutating operation requires an idempotency key.

## Package layout

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

[cursor-implementation.md](cursor-implementation.md) is the **historical** original
implementation blueprint. Current guidance is this document, the README, the
roadmap, and the MCP contract.

## Data domains

The physical schema is divided into Ontology, Knowledge, Provenance, Governance,
Operations (including write clarification), Reasoning, and optional derived
embeddings. Table inventory: [data-model.md](data-model.md). Semantic contract:
[ontology-model.md](ontology-model.md).

## Transaction principles

Write services define explicit transaction boundaries for identity resolution plus
creation, duplicate detection plus assertion, supersession, entity merge, ontology
application, clarification resume, and idempotency processing. Idempotency
reservation and operation execution must be coordinated so retries cannot
double-write. Dry-run uses a rolled-back savepoint for knowledge writes.

## Readiness

`/health/live` checks process liveness. `/health/ready` checks database
connectivity and migration compatibility. External semantic review availability is
not a knowledge-plane readiness dependency.

## Explicit non-goals for v1

No RDF store, OWL reasoner, SPARQL endpoint, autonomous entity merging,
probabilistic truth engine, UI, graph database migration, required vector
dependency, dynamic SQL schema creation, distributed event bus, or multi-node
clustering.

## Related documents

- [Design thesis](design-thesis.md)
- [MCP contract](mcp-contract.md)
- [Hybrid retrieval v1](retrieval-hybrid-v1.md)
- [Invariants](invariants.md)
