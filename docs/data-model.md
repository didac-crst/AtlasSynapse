# v1 physical data model inventory

The initial Alembic migration must create these tables. Column-level definitions are the implementation contract supplied in the project brief; this inventory groups them by logical domain so schema review stays explicit.

## Ontology

`ontology_namespace`, `ontology_class`, `ontology_class_revision`, `ontology_class_parent`, `ontology_predicate`, `ontology_predicate_revision`, `ontology_predicate_domain`, `ontology_predicate_range`, `ontology_constraint`, and `ontology_alias`.

Classes and predicates use namespace/key uniqueness. Revisions are immutable. Parent links are unique by child/parent and must remain acyclic. An alias has exactly one target, matching its target type.

## Knowledge

`entity`, `entity_type`, `entity_alias`, `statement`, and `statement_qualifier`.

Entities are preserved through merge and deprecation states. Statements have a subject, predicate, exactly one typed object, temporal fields, lifecycle status, and actor. Historical statement references are not rewritten by entity merge in v1.

## Provenance

`source`, `statement_evidence`, and `external_reference`.

Sources can be reused across statements. Evidence can contain excerpts and locators. External references are unique by source system and external ID.

## Governance

`ontology_proposal`, `ontology_gate_result`, and `ontology_change`.

Proposal status and gate decisions are auditable. Accepted changes record the affected object and previous/new revisions.

## Operations

`ingestion_batch`, `operation_log`, and `idempotency_record`.

Mutation operation logs distinguish started, success, rejected, and failed. Idempotency records are unique by actor and key and retain a request hash.

## LLM observability

`llm_call_log` is a durable provider-call audit table, separate from `operation_log`. Each call is one row: created when the call starts (`started`) and updated in place on completion with final status, outcome, tokens, duration, and cost. Rows are retained (not hard-deleted in normal operation); this is not a multi-event append stream that keeps a separate immutable start snapshot. Records include correlation IDs, provider/model/purpose, pricing snapshot/version, and redacted metadata. Raw prompts and responses are not stored by default.

## Agent feedback

`agent_feedback` stores actionable quality observations from agents (errors, ontology gaps, usability, etc.). It is not an operation failure log and not an ontology proposal. Rows link optionally to operation/proposal/entity/statement/source, carry a fingerprint for open-row dedupe, and track resolution status separately from mutation outcomes.

## Reasoning and derived data

`conflict` is authoritative reasoning metadata. `embedding` is optional derived data and may be deferred until after the base migration. It must not be required for startup or correctness.

## Baseline indexes

Create conventional indexes for entity names/status, aliases, entity types, statement subject/predicate/object/status/time, evidence links, ontology namespace/key and aliases, source external ID/content hash, operation request/trace/status/error, and conflict statement/status fields. Add PostgreSQL-specific or trigram indexes only when justified by measured lookup needs.
