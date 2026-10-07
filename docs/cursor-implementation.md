# Cursor implementation guide

> **Historical document.** This is the original implementation blueprint from the
> early build sequence. It is not current development guidance.
>
> For what AtlasSynapse is now, see the [README](../README.md),
> [architecture.md](architecture.md), [roadmap.md](roadmap.md), and
> [mcp-contract.md](mcp-contract.md).

---

This document was the low-level handoff for the developer implementing AtlasSynapse.

## Coding rules

- Use timezone-aware UTC timestamps.
- Use explicit ORM table names and keep business logic out of ORM models.
- Keep persistence in repositories and semantic rules in services.
- Keep MCP translation thin.
- Require actor context, request ID, and idempotency key for public mutations.
- Provide no hard-delete repository methods.
- Do not expose generic SQL execution outside migration/admin internals.
- Raise typed domain exceptions and translate them at transport boundaries.

## Package boundaries

```text
src/semantic_memory/
├── models/          ORM models and enums
├── schemas/         Pydantic request/response/error schemas
├── repositories/    persistence operations
├── services/        identity, statements, provenance, ontology, search
├── validation/      deterministic semantic validation
├── mcp/             thin typed tool adapter
├── api/             health and HTTP wiring
└── observability/   logging, tracing, metrics
```

## Required implementation sequence

1. enums, configuration, database session;
2. ORM models;
3. initial migration and deterministic seed;
4. repositories;
5. actor, operation log, and idempotency;
6. identity and entity services;
7. literal abstraction and statement validation;
8. statement lifecycle and provenance;
9. conflicts;
10. ontology read and proposal services;
11. deterministic gates and semantic reviewer interface;
12. batch and search services;
13. MCP tools and health API;
14. optional embeddings.

Each step must leave the repository runnable and appropriately tested.

## Critical work units

Entity resolution returns `CREATE`, `REUSE`, or `AMBIGUOUS` in the order external reference, canonical exact match, alias exact match, then candidate discovery. It never silently merges.

Statement insertion validates predicate, subject, domain, object kind, range, cardinality, interval, normalization, and semantic duplicate identity before persistence.

Ontology gates execute in this order: schema, authorization, existing key, alias, structural, cycle, domain/range, similarity, semantic review when needed, final deterministic validation, and transactional apply.

## Test expectations

Before broad MCP expansion, cover entity creation/reuse, typed relationship assertion, invalid domain/range, duplicate assertion, evidence attachment, supersession, retraction, conflicts, unknown predicate handling, proposals, cycle rejection, revision conflicts, idempotency replay/reuse, rollback logging, and rejected versus failed operation status.

## Definition of done

The first implemented slice must support a Person and Organization, a typed relationship between them, source evidence, neighborhood retrieval, typed unknown-predicate rejection, and a follow-up ontology proposal. No LLM provider may be required for this path.
