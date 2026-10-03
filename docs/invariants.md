# Invariants

These rules are implementation acceptance criteria. They must be protected by a combination of PostgreSQL constraints, service validation, transaction boundaries, and transport contracts.

1. No MCP operation hard-deletes knowledge or ontology rows.
2. No MCP operation executes arbitrary SQL or DDL.
3. Ontology concepts evolve as data.
4. Routine knowledge writes do not require an LLM.
5. Ontology mutation passes deterministic validation.
6. Semantic review can recommend but cannot bypass deterministic validation.
7. Entity duplicates are never silently auto-merged.
8. Statements are append/supersede/retract oriented.
9. Temporal validity is separate from assertion time.
10. Provenance is preservable.
11. Repeated identical writes are idempotent.
12. Conflicting claims may coexist.
13. Embeddings are derived and never authoritative.
14. Rich relationships and events are entities when needed.
15. Every mutation has an actor.
16. Failed and rejected writes are observable.
17. Ontology mutations use optimistic concurrency.
18. Database constraints protect critical invariants independently of application logic.

## Critical database constraints

- UUID primary keys and foreign keys;
- unique namespace/key pairs for classes and predicates;
- immutable revision uniqueness by parent object and revision number;
- exactly one statement object column populated;
- exactly one qualifier object column populated;
- valid intervals where `valid_to >= valid_from`;
- confidence and reliability values bounded between 0 and 1;
- alias target check requiring exactly one target and matching target type;
- unique actor/idempotency key pair;
- unique external reference system/id;
- unique embedding identity when embeddings are enabled.

## Rejection semantics

`rejected` means a deterministic refusal. `failed` means a technical or infrastructure failure. Both are retained in operation history with stable error codes and request IDs.
