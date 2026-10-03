# Development roadmap

## Phase 0 — Repository and architectural skeleton

Establish the Python project, PostgreSQL development environment, migration framework, configuration, logging, health checks, CI, documentation, ADRs, and package boundaries.

Exit: PostgreSQL starts locally and live/readiness endpoints are defined. No semantic functionality is required.

## Phase 1 — Stable physical data model

Implement the complete v1 schema, foreign keys, checks, indexes, initial migration, and deterministic core ontology seed.

## Phase 2 — Knowledge-plane core

Implement actors, entity creation and typing, aliases, external references, identity resolution, normalization, assertion, duplicate detection, and the first read/write MCP tools.

## Phase 3 — Provenance and memory quality

Implement sources, evidence, source deduplication, evidence retrieval, and statement explanation.

## Phase 4 — Temporal knowledge and history

Implement validity intervals, assertion/observation semantics, supersession, retraction, and timelines.

## Phase 5 — Idempotency and operational hardening

Implement operation logs, request/trace IDs, typed errors, retry classification, transaction wrappers, and payload redaction.

## Phase 6 — Conflicts and ambiguity

Implement conservative conflict detection, ambiguity results, explicit entity merging, and conflict retrieval.

## Phase 7 — Rich events and relationship contexts

Prove that Employment, Residence, MoveEvent, ProjectParticipation, ExperimentRun, Decision, and Observation can be represented as entities and statements without domain-specific SQL tables.

## Phase 8 — Batch ingestion

Implement `ingestion_batch`, `assert_batch`, preloading, bounded query behavior, and atomic batch handling.

## Phase 9 — Ontology read plane

Implement ontology lookup, search, context, aliases, and inheritance traversal.

## Phase 10 — Governed ontology proposals

Implement proposals, deterministic gates, gate results, changes, revision control, and proposal tools.

## Phase 11 — Semantic review adapter

Add provider-neutral semantic review interfaces with mock and disabled implementations. The reviewer never mutates the database or bypasses deterministic validation.

## Phase 12 — Optional semantic similarity

Add optional `pgvector` and embeddings only after canonical correctness works without them.

## Phase 13+ — Retrieval, ingestion policy, administration, and advanced semantic capabilities

Improve retrieval quality, decide what becomes durable memory, add inspection UI if justified, and consider exports, inference, and ontology health tooling.

## Milestones

- Milestone A: phases 0–5, usable deterministic memory.
- Milestone B: phases 6–9, semantic graph memory.
- Milestone C: phases 10–12, governed self-evolving ontology.
- Milestone D: phases 13+, mature personal knowledge substrate.

## Stop conditions

Pause feature work for architecture review if semantic concepts require SQL migrations, LLM calls enter routine writes, agents bypass proposals, provenance becomes incomplete, hard deletes become ordinary, embeddings become required for correctness, conflicts destroy alternatives, or MCP accumulates business logic.
