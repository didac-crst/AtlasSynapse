# MCP contract

The MCP adapter exposes semantic operations only. It must not expose raw SQL, DDL, hard-delete operations, or ORM objects.

The production transport is stdio via `semantic-memory-mcp` (MCP 2024-11-05 newline-delimited JSON-RPC). Tool handlers stay thin translations over services; HTTP MCP transport is not implemented yet.

## Read tools

```text
get_entity
search_entities
get_entity_neighborhood
get_timeline
search_statements
get_statement
explain_statement
find_conflicts
search_semantic_memory
get_relevant_context
get_class
get_predicate
search_ontology
get_ontology_context
get_proposal
```

## Knowledge write tools

```text
create_entity
assert_statement
assert_batch
supersede_statement
retract_statement
add_evidence
merge_entity
```

## Ontology proposal tools

```text
propose_class
propose_predicate
propose_constraint
propose_alias
propose_class_parent
```

## Request rules

Every mutation accepts actor context, request ID, and an idempotency key. The adapter validates request schemas and delegates to services. Service results are translated into typed response schemas.

## Error envelope

Every error contains:

```json
{
  "error_code": "UNKNOWN_PREDICATE",
  "message": "...",
  "details": {},
  "request_id": "uuid",
  "retryable": false
}
```

Unknown predicates do not create ontology implicitly. They may include suggested predicates and can be followed by an ontology proposal.

## Batch result

`assert_batch` defaults to atomic behavior and returns created, reused, ambiguous, rejected, and ontology-required items explicitly. Partial acceptance is a future opt-in behavior.

## Capabilities

The expected default ChatGPT actor capabilities are `knowledge.read`, `knowledge.write`, `ontology.read`, and `ontology.propose`. Direct ontology application is reserved for the governance controller.
