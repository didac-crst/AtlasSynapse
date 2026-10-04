# Development roadmap

## Phase 0 — Repository and architectural skeleton

Status: Done.

Establish the Python project, PostgreSQL development environment, migration framework, configuration, logging, health checks, CI, documentation, ADRs, and package boundaries.

Exit: PostgreSQL starts locally and live/readiness endpoints are defined. No semantic functionality is required.

## Phase 1 — Stable physical data model

Status: Done.

Implement the complete v1 schema, foreign keys, checks, indexes, initial migration, and deterministic core ontology seed.

## Phase 2 — Knowledge-plane core

Status: Done.

Implement actors, entity creation and typing, aliases, external references, identity resolution, normalization, assertion, duplicate detection, and the first read/write MCP tools.

## Phase 3 — Provenance and memory quality

Status: Done.

Implement sources, evidence, source deduplication, evidence retrieval, and statement explanation.

Evidence retrieval is provided by `explain_statement` (source, excerpt, locator, assertion/observation times). A separate evidence list/get endpoint remains optional.

## Phase 4 — Temporal knowledge and history

Status: Done.

Implement validity intervals, assertion/observation semantics, supersession, retraction, and timelines.

## Phase 5 — Idempotency and operational hardening

Status: Done.

Implement operation logs, request/trace IDs, typed errors, retry classification, transaction wrappers, and payload redaction.

Knowledge-plane mutations (entity create, statement assert/supersede/retract, source ensure, evidence add) go through the audited `MutationRunner` path.

Phases 3–5 were delivered together as one milestone: auditable, temporal, retry-safe statements.

## Phase 6 — Conflicts and ambiguity

Status: Done.

Implement conservative conflict detection, ambiguity results, explicit entity merging, and conflict retrieval.

Overlapping cardinality-one statements coexist with recorded conflict metadata. `find_conflicts`, resolve/dismiss, and explicit `merge_entity` preserve history without automatic retraction or merging.

## Phase 7 — Rich events and relationship contexts

Status: Done.

Prove that Employment, Residence, MoveEvent, ProjectParticipation, ExperimentRun, Decision, and Observation can be represented as entities and statements without domain-specific SQL tables.

## Phase 8 — Batch ingestion

Status: Done.

Implement `ingestion_batch`, `assert_batch`, preloading, bounded query behavior, and atomic batch handling.

`assert_batch` returns created, reused, ambiguous, rejected, and ontology-required results explicitly.

## Phase 9 — Ontology read plane

Status: Done.

Implement ontology lookup, search, context, aliases, and inheritance traversal.

Phases 6–9 were delivered together as Milestone B: semantic graph memory.

## Phase 10 — Governed ontology proposals

Status: Done.

Implement proposals, deterministic gates, gate results, changes, revision control, and proposal tools.

## Phase 11 — Semantic review adapter

Status: Done.

Add provider-neutral semantic review interfaces with mock and disabled implementations. The reviewer never mutates the database or bypasses deterministic validation.

## Phase 12 — Optional semantic similarity

Status: Done.

Add optional embeddings with a provider interface and JSONB vector storage. `pgvector` is not required. Embeddings never determine truth, identity, or acceptance.

## Phase 13 — Retrieval and LLM observability

Status: Done.

Implement database-backed retrieval with transparent ranking signals, and durable `llm_call_log` observability for provider calls (separate from `operation_log`; one begin/complete row per call). Retrieval must work without an LLM or vector provider. Every semantic-review call must be measurable.

## Phase 14 — Production readiness

Status: Done.

Package the application for deployment before connecting real users or agents:

- Docker image and Compose `api` / `migrate` (and optional `mcp`) services
- Production configuration and secret fail-closed checks
- HTTP authentication boundary for non-health routes
- Migration job behavior separate from API startup
- Real MCP stdio transport over thin tool adapters
- Backup/restore, TLS/proxy, and observability documentation
- Deployment smoke tests and a committed dependency lockfile

## Phase 15a — Agent feedback channel

Status: Done.

Add a first-class `agent_feedback` channel for dogfooding: agents report quality observations via MCP/HTTP without conflating them with `operation_log`, `llm_call_log`, or ontology proposals. Include fingerprint dedupe for open rows, redacted context, and admin list/resolve.

## Phase 15+ — Ingestion policy, administration, and advanced semantic capabilities

Status: Next.

Decide what becomes durable memory (ingestion policy), add inspection UI if justified, and consider exports, inference, and ontology health tooling. Prefer evidence from dogfooding and agent feedback over speculative automation.

## Milestones

- Milestone A: phases 0–5, usable deterministic memory. — Done.
- Milestone B: phases 6–9, semantic graph memory. — Done.
- Milestone C: phases 10–12, governed self-evolving ontology. — Done.
- Milestone D: phase 13 retrieval/LLM observability. — Done.
- Milestone E: phase 14 production readiness. — Done.
- Milestone F: phase 15a agent feedback done; remaining 15+ ingestion policy, administration, and advanced capabilities. — Next.

## Stop conditions

Pause feature work for architecture review if semantic concepts require SQL migrations, LLM calls enter routine writes, agents bypass proposals, provenance becomes incomplete, hard deletes become ordinary, embeddings become required for correctness, conflicts destroy alternatives, or MCP accumulates business logic.
