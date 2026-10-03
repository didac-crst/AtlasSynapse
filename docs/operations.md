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
admin
```

The normal ChatGPT integration receives the first four except `ontology.apply`.

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

## Health behavior

`/health/live` reports process liveness. `/health/ready` verifies database connectivity and migration compatibility. The readiness check must not require an external LLM or embedding provider.
