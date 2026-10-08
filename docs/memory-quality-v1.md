# Memory Quality v1 — Checkpoint design pack

**Status:** approved for checkpoint 1 implementation (architecture locked).

**Branch:** `feat/memory-quality-v1` from `main`.  
Do not modify retrieval hybrid v1 ranking behavior beyond optional relevance-bounded metadata.

**Product goal:** a memory-quality control layer that detects suspicious graph state
after writes, routes each problem to the correct existing mechanism, and surfaces
unresolved issues through MCP so ChatGPT can act as the human interface. There is
no dedicated HMI in v1.

```text
WRITE
  ↓
existing governance
  ↓
commit path (mutation body)
  ↓
bounded quality inspection (separate short transaction / savepoint)
  ↓
├─ already owned → conflict / existing mechanism
├─ deterministic fix → governed mutation + audit (no quality issue row)
└─ unresolved residual → memory_quality_issue
                          ↓
              mutation response quality_warnings NOW
                          +
              retrieval quality_warnings LATER
                          ↓
                    ChatGPT = HMI
```

```text
detect → route → surface → clarify if needed → resolve through existing governance
```

---

## 1. Ownership map

Before creating a `memory_quality_issue`, ask: does an existing mechanism already
own this problem?

| Mechanism | Owns | Does not own |
| --- | --- | --- |
| **`conflict`** | Competing effective / cardinality-sensitive statement pairs; open/resolve/dismiss lifecycle already in DB + MCP | Residual post-write problems with no conflict owner; supersession graph corruption; entity-identity suspicion; provenance debt |
| **`write_clarification_request`** | Ambiguity that **blocks or affects a write in flight** | Problems discovered **after** a successful write |
| **Identity resolution / `merge_entity`** | Server-owned identity on writes; explicit merge | Post-write “these two existing entities look similar”; durable `A ≠ B` (not modeled yet) |
| **`agent_feedback`** | Agent/user-**reported** observations | System-detected graph quality |
| **`memory_quality_issue` (new)** | System-detected **post-write residual** conditions with **no better existing owner** | A second conflict table; a second write/clarification system; a parallel mutation API |

### Routing intent (summary)

| Finding class | Owner / route |
| --- | --- |
| Competing effective facts / cardinality clash | **`UPSERT_CONFLICT` only** → existing `ConflictService`. **Never** a `memory_quality_issue` type |
| Write-time identity ambiguity | **Write clarification** (not quality) |
| Structurally identical duplicate statements | Prevented on normal assert via `find_semantic_duplicate` (**REUSE**). Sync post-write detector: **not used**. Future reconciliation / legacy audit only |
| Supersession structural integrity | **`OPEN_QUALITY_ISSUE`** (or immediate governed fix + audit with **no issue row** if deterministic) |
| Possible duplicate entities (post-write) | Deferred |
| Weak / missing provenance | Deferred (observational) |
| Agent opinion / UX complaint | **`agent_feedback`** only |

`memory_quality_issue` is a **narrow residual control-plane object**, not a universal inbox.

**Conflict ownership is absolute:** there is no `competing_effective_facts` quality issue type and no `related_conflict_id` column.

---

## 2. `memory_quality_issue` schema

Table: `memory_quality_issue`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `issue_type` | enum/text | See issue types |
| `status` | enum | `open` \| `resolved` \| `dismissed` |
| `severity` | enum | `info` \| `low` \| `medium` \| `high` \| `critical` |
| `detector_key` | text | Provenance, e.g. `supersession_integrity` |
| `detector_version` | text | Provenance only — **not** in fingerprint |
| `fingerprint` | text not null | Problem identity; unique among open rows |
| `subject_entity_id` | UUID FK nullable | |
| `object_entity_id` | UUID FK nullable | |
| `statement_id` | UUID FK nullable | |
| `related_entity_ids` | JSONB | Bounded list of UUID strings |
| `related_statement_ids` | JSONB | Bounded list of UUID strings |
| `summary` | text | Short agent-facing explanation |
| `evidence` | JSONB | Bounded structured evidence |
| `requires_clarification` | boolean | Hint for ChatGPT |
| `trigger_operation_id` | UUID FK nullable → `operation_log` | |
| `last_seen_operation_id` | UUID FK nullable | |
| `occurrence_count` | int | Dedupe refresh counter |
| `resolution` | enum nullable | **null while `open`** |
| `resolution_operation_id` | UUID FK nullable | |
| `resolved_by_actor_id` | UUID FK nullable | |
| `resolved_at` | timestamptz nullable | |
| `created_at` / `updated_at` | timestamptz | |

### Status / resolution

While unresolved: `status = open`, `resolution = null`.

Terminal status: `resolved` | `dismissed`.

Terminal resolutions: `auto_resolved` (reserved for closing after governed fix when an issue was already open), `corrected`, `retracted`, `superseded`, `confirmed_same` (deferred), `confirmed_different` (deferred), `unknown`, `no_action`.

**AUTO_RESOLVE policy:** if a finding is deterministically fixed immediately via an existing governed mutation, **do not create a quality issue row**. Record the fix in the normal operation/audit trail only. `memory_quality_issue` holds **unresolved residual** problems.

### Issue types (checkpoint 1)

| `issue_type` | Checkpoint 1 |
| --- | --- |
| `supersession_integrity` | **Yes** (sync detector) |
| `possible_duplicate_entity` | Enum reserved; detector deferred |
| `weak_or_missing_provenance` | Enum reserved; detector deferred |
| ~~`duplicate_statement`~~ | Not a sync issue type for hot path; structural definition retained for future reconciliation |
| ~~`competing_effective_facts`~~ | **Removed** — conflicts only |

### Indexes

- unique partial: `(fingerprint) WHERE status = 'open'`
- `status`, `issue_type`, `subject_entity_id`, `statement_id`, `updated_at`

### UNKNOWN invariant (document now; full enforcement may be deferred)

A terminal `resolution = unknown` **suppresses** the same fingerprint from re-opening
until **materially new evidence** appears (evidence hash / related ID set change).
Otherwise ChatGPT would re-ask forever after NOT SURE.

---

## 3. Routing outcomes (post-write)

```text
QualityFinding
    ↓
route_finding()
    ↓
IGNORE
OPEN_QUALITY_ISSUE
UPSERT_CONFLICT
IDENTITY_REVIEW      # deferred usage
AUTO_RESOLVE         # governed mutation + audit; no issue row
```

**Removed from post-write router:** `REQUEST_CLARIFICATION`.  
Clarification remains write-in-flight only. Post-write ambiguity →
`OPEN_QUALITY_ISSUE` with `requires_clarification=true`.

| Outcome | Behavior |
| --- | --- |
| `IGNORE` | No side effect |
| `OPEN_QUALITY_ISSUE` | Upsert open issue by fingerprint |
| `UPSERT_CONFLICT` | Existing `ConflictService` only |
| `IDENTITY_REVIEW` | Later: open `possible_duplicate_entity` issue |
| `AUTO_RESOLVE` | Existing governed mutation + audit; **no** quality issue row |

Detectors emit findings only. Router + quality service own side effects.

---

## 4. Post-write trigger and transaction boundaries

```text
MutationRunner (execute path only)
        ↓
mutation body
        ↓
post_execute: bounded quality inspection (nested savepoint)
        ↓
attach quality_warnings to response
        ↓
cache final response (incl. warnings) + audit
        ↓
outer commit (API/MCP)

idempotent replay → return cached final response (no quality re-inspect)
dry_run → no post_execute / no durable quality rows
```

### Guarantees

1. Quality failure must **not** roll back a valid primary mutation (rollback quality savepoint only).
2. Neighborhood-scoped `change_context` only — never a full graph scan after each write.
3. Dry-run: no durable quality issues.
4. Detector exceptions logged; primary write remains success.
5. Quality work is not inside the primary mutation’s idempotency reservation semantics beyond sharing the outer request commit.

### `change_context`

```text
actor_id, operation_id?, operation_name
touched_entity_ids[], touched_statement_ids[], touched_predicate_ids[]
supersession_edges[]  # previous → successor when applicable
skip_fingerprints[]   # non-reentrancy
dry_run: bool
```

### Hook placement

After successful knowledge mutations that change effective graph state:

- `assert_statement` / batch assert
- `correct_statement` / `supersede_statement` / `retract_statement`

Not: ontology apply, feedback, admin reads. Dry-run skips durable quality writes.

---

## 5. `quality_warnings` contracts

### Mutation responses (primary interrupt)

Add `quality_warnings` to the standard statement mutation envelopes
(`AssertStatementResponse`, `SupersedeStatementResponse`, `RetractStatementResponse`,
and correct path as applicable):

```json
{
  "quality_warnings": [
    {
      "issue_id": "uuid",
      "type": "supersession_integrity",
      "severity": "high",
      "summary": "...",
      "requires_clarification": true,
      "related_entity_ids": ["..."],
      "related_statement_ids": ["..."]
    }
  ]
}
```

No new MCP tool. ChatGPT sees problems at write time.

### Retrieval (later safety net)

Same field on `SearchSemanticMemoryResponse` and `RelevantContextResponse`,
relevance-bounded to returned entities/statements. Cap (e.g. 5). No LLM.

Competing facts continue to surface via existing conflict hits / `include_conflicts`.
Do not mirror conflicts as quality warnings.

---

## 6. ChatGPT / MCP interaction flow

```text
mutation returns facts + quality_warnings (if any)
        and/or
retrieval returns facts + quality_warnings (if relevant)
        ↓
ChatGPT explains to the user
        ↓
user clarifies
        ↓
existing governed tools (correct / retract / supersede / conflict resolve / …)
        ↓
issue closed with resolution (+ resolution_operation_id) when applicable
```

No default-agent quality CRUD. No `get_memory_health` in checkpoint 1.

---

## 7. Dedupe and non-reentrancy

### Fingerprint (problem identity)

```text
issue_type
+ detector_key
+ sorted entity UUID strings
+ sorted statement UUID strings
```

**Exclude `detector_version`** from the fingerprint. Store version as provenance only.

Open-row uniqueness: partial unique index on `fingerprint` where `status = 'open'`.

Repeat detection: bump `occurrence_count`, refresh `evidence`, `updated_at`,
`last_seen_operation_id`.

### Non-reentrancy

1. AUTO_RESOLVE must not reopen the same problem in the same request (`skip_fingerprints`).
2. Detectors are idempotent for the same neighborhood.
3. Max one auto-resolve attempt per fingerprint per triggering operation.
4. Closed `unknown` suppresses same fingerprint until materially new evidence (invariant; enforcement may land after checkpoint 1).

---

## 8. Reused vs new APIs

**Reuse:** `ConflictService`, statement mutations, `MutationRunner`, `operation_log`,
retrieval envelopes, admin inspection patterns, `find_semantic_duplicate` (definition /
future reconciliation only).

**New:** `memory_quality_issue` table, detector protocol, `QualityFinding` + router,
`MemoryQualityService`, post-write hook, `quality_warnings` fields, admin list/get.

**Forbidden:** `resolve_quality_issue` as a graph-mutating API; parallel conflict domain.

---

## 9. Duplicate statements (locked)

**Structural equality** (same as `find_semantic_duplicate`):

```text
same subject_entity_id, predicate_id, normalized_object,
valid_from / valid_to (null-safe), status = asserted
```

Normal assert already **REUSE**s structural duplicates — they cannot appear via
governed writes. Therefore:

- **No sync post-write `duplicate_statement` detector**
- Reserve scanning for future **reconciliation / legacy-data audit**

---

## 10. Checkpoint 1 scope

1. Schema + service + routing skeleton  
2. **`supersession_integrity` detector only** (sync)  
3. Conflict integration via existing `ConflictService` only (no quality rows)  
4. Post-write hook + separate short transaction/savepoint  
5. `quality_warnings` on mutation responses  
6. Relevance-bounded `quality_warnings` on retrieval  
7. Basic admin inspection  
8. Dedupe / non-reentrancy tests  
9. Mutation latency measurement before/after  

### Out of scope

- Possible duplicate entity detector / merge UX / negative identity  
- Sync duplicate-statement detector  
- LLMs / `get_memory_health` / scheduler / full reconciliation job  
- Retrieval ranking tuning  
- `REQUEST_CLARIFICATION` from quality router  

---

## 11. Acceptance scenarios

| Scenario | Expected |
| --- | --- |
| A — Structural duplicate write | Assert **REUSE**; no quality issue from sync path |
| B — Competing current facts | Existing **conflict** owns it |
| C — Possible duplicate identity | Deferred |
| D — User never answers | Issue stays open or closes `unknown`; retrieval usable |
| E — Retrieval touches affected entity | Facts + relevant `quality_warnings` |
| F — Write creates supersession integrity problem | Mutation response includes `quality_warnings` immediately |

---

## 12. Definition of success

> Detect that something may be wrong, store why, route to the correct existing
> mechanism, expose unresolved ambiguity through ChatGPT when useful, and never
> change truth unless an existing governed mutation authorizes it.

---

## Related documents

- [architecture.md](architecture.md)
- [invariants.md](invariants.md)
- [operations.md](operations.md)
- [mcp-contract.md](mcp-contract.md)
- [design-thesis.md](design-thesis.md)
- [business-value.md](business-value.md)
