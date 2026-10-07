# Mutation result context (`feat/mutation-result-context`)

## Principle

A mutation response describes the **state transition**, not merely an ack.
ChatGPT should usually finish after `write(return_mode=standard)` without an
immediate read-back.

## `return_mode` (statement mutations only)

On `AssertStatementRequest` / `CorrectStatementRequest` /
`SupersedeStatementRequest` / `RetractStatementRequest` — **not** on generic
`MutationEnvelope`.

| Mode | Default | Returns |
| --- | --- | --- |
| `minimal` | no | outcome + ids / statement_action |
| `standard` | **yes** | statement, identities, changes, effective_state, actionable CLARIFY |
| `contextual` | no | standard + fixed related slice |

### `effective_state`

- Subject + predicate scoped (survivor subject)
- Effective only (asserted, non-superseded, non-retracted)
- Entity-valued vs literal distinguished (`value_kind`, `entity_id`+`canonical_name` vs literals)
- Deterministic order: `asserted_at` desc, then `statement_id`
- Cap 20 values + `truncated: true`

### `contextual` contract (fixed)

- Max **5** related facts
- Effective only
- One-hop from subject and/or object of the resulting statement
- Ranked with the neighborhood scorer
- **No** recursive `get_relevant_context`

### CLARIFY

Includes `question`, `reason`, `candidates`, `resume_with=answer_identity_clarification`
so the agent can ask the user without another tool call.
