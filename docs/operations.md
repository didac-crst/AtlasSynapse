# Operational contract

## Actors and capabilities

Operational actors are separate from knowledge entities. Actor types are `user`, `agent`, `service`, `system`, and `admin`; statuses are `active` and `disabled`.

Capability groups are:

```text
knowledge.read
knowledge.write
ontology.read
ontology.propose
ontology.apply
feedback.create
feedback.read
feedback.manage
admin
```

The normal ChatGPT MCP actor receives knowledge read/write, ontology read/propose/apply, and `feedback.create`. Feedback administration remains reserved for privileged actors.

## Mutation envelope

Every public mutation carries or receives:

```text
actor
request_id
idempotency_key
trace_id (optional)
```

The service reserves the idempotency key with a request hash before executing the write. A matching retry returns the stored response. A changed payload returns `IDEMPOTENCY_KEY_REUSED`.

## Audit behavior

Every mutation creates an operation record. Deterministic refusal is `rejected`; infrastructure or unexpected errors are `failed`; completed work is `success`. Structured audit fields are retained indefinitely. Raw payload retention is configurable as `none`, `redacted`, or `full`, with `redacted` as the default. Secrets and authorization tokens are never logged.

## Ontology review behavior

The semantic reviewer is a replaceable protocol. Disabled or unavailable review returns `manual_review` for ontology proposals and does not affect knowledge-plane readiness. Reviewers cannot write to the database, invoke DDL, or bypass deterministic gates.

Every semantic-review invocation is recorded in `llm_call_log` (not `operation_log`) with request/trace/operation correlation when available. Logging uses a begin/complete lifecycle on a single row per call (start insert, completion update)—not a separate immutable start-event row. Statuses include `started`, `succeeded`, `failed`, `unavailable`, and `manual_review`. Cost is estimated as `input_tokens * input_rate + output_tokens * output_rate` with a persisted pricing snapshot/version, or stored as provider-reported. Unknown cost remains NULL/`unknown` rather than a fabricated zero. Raw prompts and completions are not persisted by default; metadata follows the payload-retention policy.

## Dry-run mutations

`dry_run=true` on knowledge and ontology apply mutations runs the **same** identity, validation, and semantic-review path inside a rolled-back savepoint. Knowledge, provenance, and idempotency records are not persisted; operation logs may record the dry-run. Response fields such as `operation_mode`, `would_persist`, and `WOULD_*` statement actions distinguish previews from commits. See invariants 20–22.

## Write clarification lifecycle

Identity ambiguity on statement writes issues a `write_clarification_request` handle (control-plane state). Callers resume with `answer_identity_clarification` (`chosen_entity` / `create_new` / `reject`). Handles are **one-shot**, expire (~30 minutes), and may be superseded when a fresh clarification is required. Before resume, AtlasSynapse rechecks current production state. Terminal rows are GC'd after retention (~7 days). A handle created under dry-run remains dry-run on answer.

## MCP actor injection

The MCP transport injects a server-configured `actor_key` (`MCP_ACTOR_KEY`, default `chatgpt`) into every mutation envelope and ensures that actor exists. MCP clients must not supply `actor_key`. HTTP callers still pass `actor_key` explicitly. Tool surfaces (`MCP_TOOL_SURFACE`) filter catalog visibility only; capabilities still authorize writes. See [mcp-contract.md](mcp-contract.md) and [mcp-agent-surface.md](mcp-agent-surface.md).

## Retrieval behavior

Retrieval (`search_entities`, `search_statements`, `search_semantic_memory` / `search_memory`, `get_relevant_context`, neighborhood/timeline/explain/conflicts) is database-backed and must succeed without an LLM or embedding provider. Hybrid retrieval v1 combines tokenized lexical matching, temporal/effective-state ranking, soft predicate intent, and bounded historical anchors; ranking exposes transparent signals and must not collapse into an unexplained truth score. Vector search remains optional. See [retrieval-hybrid-v1.md](retrieval-hybrid-v1.md).

## Agent feedback

Agents may report quality observations via `report_feedback` / `POST /v1/feedback` without treating a successful operation as a failure. Feedback lives in `agent_feedback`, separate from `operation_log`, `llm_call_log`, and ontology proposals. Open rows with the same fingerprint are deduplicated by incrementing `occurrence_count`. Context payloads follow the same retention/redaction policy as operation audits. Listing and resolution require `feedback.read` / `feedback.manage` (or `admin`).

## Memory quality (post-write)

After successful knowledge mutations, AtlasSynapse runs a **neighborhood-scoped**
deterministic quality inspection in a nested savepoint (failures do not roll back
the primary write). Residual problems open `memory_quality_issue` rows; competing
effective facts remain owned exclusively by `conflict`. Warnings appear on the
mutation response immediately and again on retrieval when relevant
(`quality_warnings`). ChatGPT is the HMI — there is no default MCP quality CRUD.
Open issues can be healed via `repair_supersession_integrity` (issue_id +
explicit successor); the issue closes only after revalidation.
See [memory-quality-v1.md](memory-quality-v1.md).

## Health behavior

`/health/live` reports process liveness. `/health/ready` verifies database connectivity and migration compatibility. The readiness check must not require an external LLM or embedding provider.

## HTTP authentication

Application HTTP routes require a shared API token (`Authorization: Bearer` or `X-API-Token`) when `HTTP_API_TOKEN` is configured, and always when `APP_ENV=production`. Health probes (`/health`, `/health/*`) and documentation routes (`/docs`, `/openapi.json`, `/redoc`, when enabled) stay public. `actor_key` remains an audit/capability identifier, not the authentication secret. Administrative routes (`POST /v1/actors/ensure` and `/v1/admin/*`) additionally require `X-Admin-Token` (missing/invalid → 403; missing outer API token → 401).

## Admin inspection

`/v1/admin/*` is HTTP-only, read-only observability for humans/ops: list/get filters over operations, LLM calls, feedback, ontology proposals, conflicts, ingestion batches, and memory quality issues, plus `GET /v1/admin/summary`. Collection views return metadata projections (no request/response payloads, LLM metadata, feedback context, or proposal payloads). Detail endpoints may opt in with `include_payloads=true`. Pagination is deterministic (`created_at DESC, id DESC`) with exact totals. No MCP admin tools.

## Deployment

See [deployment.md](deployment.md) for Compose packaging, migration jobs, MCP stdio transport, backup/restore, TLS/reverse-proxy expectations, and smoke checks.
