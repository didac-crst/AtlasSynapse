# Knowledge Ingestion v1 — governed semantic compilation

Design-only. Implementation starts only after this document is approved as the
implementation contract (Phase A first).

This is **not** a bulk assert API. It is a semantic compilation pipeline that
decides **what becomes durable memory** (Milestone H / ingestion policy).

```text
source
  → candidate semantic representation
  → identity / ontology / semantic resolution (dependency fixpoint)
  → governed staging
  → clarification where genuinely necessary
  → commit (Statement and/or Claim)
  → Memory Quality inspection
```

---

## Locked invariants

1. Ordinary positive facts use the existing `Statement` path.
2. Hypotheses, recommendations, questions (and unsafe assertions) become durable
   `Claim` entities — never bare domain triples pretending to be facts.
3. Claim interiors are **not** world beliefs (retrieval invariant).
4. `claimPredicateKey` is descriptive proposition structure only; it must never
   expand into an asserted domain triple. Claim promotion is out of V1.
5. Speech-act link is a single predicate: `Document --makesClaim--> Claim`, plus
   `epistemicKind`. No separate hypothesizes/recommends/posesQuestion predicates.
6. Claim identity is **source-scoped** (occurrence), never globally merged by
   proposition text/SPO.
7. Commits use `MutationRunner` + `assert_statement` (+ `add_evidence`), never
   `assert_batch`. New durable ingestion tables; do not overload `ingestion_batch`.
8. Budgets are operational guardrails, not semantic correctness. Exhaustion →
   resumable `paused`, not failure.
9. Ontology evolution only on representational necessity; reuse propose ≠ apply.
   No auto-apply from ingestion.

---

## Ontology bootstrap (minimal)

| Kind | Key | Notes |
| --- | --- | --- |
| Class | `Claim` | `Claim ⊑ Thing` |
| Predicate | `makesClaim` | **domain: Document**, range: Claim, cardinality many |
| Predicate | `claimText` | Claim → string; **mandatory** on every Claim |
| Predicate | `claimSubject` | Claim → Thing; optional structured projection |
| Predicate | `claimPredicateKey` | Claim → string; optional; non-assertive |
| Predicate | `claimObject` | Claim → Thing; optional |
| Predicate | `claimObjectString` | Claim → string; optional literal object |
| Predicate | `epistemicKind` | Claim → string |
| Predicate | `claimPolarity` | Claim → string (`positive` \| `negative`) |
| Predicate | `claimStatus` | Claim → string (lifecycle) |
| Predicate | `claimDerivation` | Claim → string |
| Predicate | `aboutEntity` | Claim → Thing; optional denorm for “claims about X” |

### `makesClaim` semantics

```text
document --makesClaim--> claim
```

means: **this source artifact presents/records this claim** — not necessarily
that the document’s author personally endorses it.

Speaker attribution (`Claim --attributedTo--> Person`) is deferred.

### Every package has a Document

Every ingestion creates or reuses a durable `Source` + `SourceContentRevision`
and a corresponding **`Document` entity** that is the `makesClaim` subject, even
when the input was raw YAML, JSON, markdown, or a conversation export.

### `epistemicKind` values (V1)

```text
hypothesis
recommendation
question
assertion          # fallback only — see commit policy
```

Normal positive factual assertions do **not** become Claims.

---

## Claim identity (invariant)

```text
Claim identity =
  source_content_revision_id
  + candidate_key
```

(or an equivalent deterministic key derived from those).

Two documents stating the same proposition → **two Claim occurrences**.

Document A rejecting a recommendation must not flip Document B’s Claim to
`rejected`.

Proposition-level clustering (`semanticallyEquivalentProposition`) is **out of
V1**.

Claims must **not** use Person/Organization-style global identity merge.

---

## Polarity vs lifecycle vs candidate epistemic status

Keep dimensions separate:

| Field | Where | Values |
| --- | --- | --- |
| `polarity` | candidate + Claim | `positive` \| `negative` |
| `claimStatus` / lifecycle | Claim (and mirrored on candidate at extract) | `active` \| `rejected` \| `superseded` \| `open` \| `answered` |
| `epistemic_status` | **candidate** (extraction output) | same lifecycle set; commit copies to `claimStatus` |

`rejected` is **not** a polarity value.

Examples:

```text
“Maybe AtlasSynapse should use AGPL.”
  kind=recommendation|hypothesis, polarity=positive, epistemic_status=active

Later rejected:
  claimStatus=rejected  (polarity stays positive)

“AtlasSynapse should not replace CRM.”
  kind=recommendation, polarity=negative, epistemic_status=active
```

`source_context_path` is **evidence explaining extraction** (e.g. under
`rejected_or_weakened_hypotheses`). Commit must **not** parse path strings
(`if "rejected" in path`). Extraction sets `epistemic_status` explicitly.

---

## Commit policy

| Candidate | Live representation |
| --- | --- |
| `kind=assertion`, `polarity=positive`, safely representable as domain fact | Ordinary `Statement` |
| `kind=assertion` that cannot safely become a domain Statement (e.g. generic negation) | `Claim` with `epistemicKind=assertion` |
| `hypothesis` / `recommendation` / `question` | `Claim` with matching `epistemicKind` |
| Inner proposition of any Claim | Never auto-asserted as a domain triple |

Strict rule:

> A hypothesis/recommendation/question (and fallback assertion Claim) must never
> be committed as though its inner proposition were an ordinary world fact.

---

## Schema (new tables)

### `knowledge_ingestion`

Run record: actor, status, source_id, source_content_revision_id, source_hash,
document_entity_id, request_id, idempotency_key, pipeline_version,
ontology_revision_marker, extraction_model / prompt_version (nullable),
budgets_json, stats_json, pause_reason (nullable), timestamps.

### `knowledge_candidate`

| Column | Notes |
| --- | --- |
| `candidate_key` | Stable within package; part of Claim identity |
| `kind` | assertion \| hypothesis \| recommendation \| question |
| `polarity` | positive \| negative |
| `epistemic_status` | active \| rejected \| superseded \| open \| answered |
| `derivation` | explicit \| normalized \| inferred |
| `claim_payload` | Normalized structure; may include SPO projections |
| `claim_text` | Normalized/original expression (required for Claim commits) |
| `source_span` | Locator / path / offsets |
| `source_context_path` | Structural ancestors — explanatory only |
| `state` | Processing state machine |
| `blockers_json` | `[{type, ref, detail}]` |
| `attempt_count` | |
| `resolution_json` | Bound ids, proposal ids |
| `committed_entity_id` | Claim entity when committed as Claim |
| `committed_statement_ids` | JSON list |

Unique `(ingestion_id, candidate_key)`.

### `knowledge_candidate_dependency`

`parent_candidate_id`, `child_candidate_id`, `dependency_kind`.

### `knowledge_ingestion_clarification`

Package-level clarification; optional links to
`write_clarification_request_id` / ontology semantic clarification id;
`impact_blocked_count`; answer payload.

### `knowledge_ingestion_effect`

| Column | Notes |
| --- | --- |
| `effect_key` | Stable within candidate, e.g. `claim:create`, `statement:makesClaim`, `statement:claimText`, `evidence:makesClaim` |
| `effect_type` | create_entity \| assert_statement \| add_evidence \| … |
| `operation_id` / `statement_id` / `entity_id` | |
| `status` | applied \| failed |

Unique `(ingestion_id, candidate_id, effect_key)`.

Operation idempotency keys are derived deterministically from
`(ingestion_id, candidate_id, effect_key)`.

Do **not** use `(candidate, effect_type)` alone — one Claim emits many
`assert_statement` effects.

---

## State machines

### Ingestion run

```text
accepted
  → extracting
  → resolving ⇄ awaiting_clarification
  → paused                    # budget_exhausted; resumable without answers
  → committing
  → completed
  → failed
```

- `continue_ingestion(ingestion_id, answers?)`
  - with answers: resolve clarifications, then resume agenda
  - without answers while `paused`: resume agenda only
- Dry-run: never `committing`; preview only; dry-run clarification handles
  cannot resume into execute (existing write-clarification rule)

### Candidate processing `state`

```text
extracted → actionable → blocked → resolved_commit_eligible → committed
                              ↘ discarded | failed
```

`epistemic_status` is orthogonal (semantic lifecycle), not a synonym of
processing `state`.

---

## Work agenda / fixpoint

```text
agenda = actionable candidates under attempt budget

while agenda and budgets_ok:
  item = pop()
  resolve(item)   # identity, ontology reuse, necessity-gated proposal, normalize
  if state changed: wake dependents

stop when:
  agenda empty
  OR only clarification/policy blockers remain
  OR budget exhausted → run status=paused
```

Budgets (attempts, LLM calls, ontology proposals, wall time) are guardrails.
Primary progress metrics: state transitions, blockers cleared, dependents woken.

---

## Clarifications

- Rank by `impact_blocked_count` (root ambiguity first).
- Identity → existing `WriteClarificationRequest` + `answer_identity_clarification`.
- Ontology → existing propose / semantic clarification / apply.
- Package-local synonym questions → `knowledge_ingestion_clarification`.
- No parallel clarification framework.

Unknown term triage before ontology proposal:

```text
existing entity → alias → instance of existing class → literal/value
  → package-local concept → representable with current ontology
  → genuine reusable gap → OntologyProposal
```

---

## Commit recipes

### Domain assertion (positive, safe)

```text
assert_statement → add_evidence → effect log → MQ post_execute
```

### Claim (hypothesis / recommendation / question / fallback assertion)

```text
ensure Claim entity (identity = revision + candidate_key)
→ assert makesClaim (Document → Claim)
→ assert claimText (mandatory)
→ optional structured bindings (subject / predicateKey / object*)
→ assert epistemicKind, claimPolarity, claimStatus←epistemic_status, claimDerivation
→ optional aboutEntity
→ add_evidence (locator = source_span)
→ effect log per effect_key
→ MQ post_execute
```

Component commit: independent dependency components may commit when fully
resolved; blocked components remain staged. Ontology apply is never automatic.

---

## Retrieval invariant (correctness)

Default:

```text
search_memory(include_claims=false)
get_relevant_context(normal entity)
  excludes:
    Claim entities
    Claim-binding statements
    makesClaim statements
```

Opt-in:

```text
include_claims=true
  may return Claim entities and epistemic graph
```

Direct Claim lookup by id (get entity / context with `entity_id=claim`) may
return Claim structure.

**Generic search must not leak Claims** merely because lexical ranking hit
claimText. “Hit is a Claim” is not an exception for default search — only
explicit `include_claims` or direct Claim id access.

`claimPredicateKey` must never be compiled into a domain SPO by retrieval or
commit.

---

## API / MCP

```text
ingest_knowledge(...)
continue_ingestion(ingestion_id, answers?)
get_ingestion(ingestion_id, include_candidates?)
```

Agent surface: all three. No candidate CRUD, force-commit, or ontology internals.

---

## Implementation phases

| Phase | Deliverable |
| --- | --- |
| **A** | Claim ontology + retrieval exclusion + invariant tests (**first**) |
| **B** | `knowledge_ingestion*` tables + effect log with `effect_key` |
| **C** | Structured extractor (YAML/JSON): kind, polarity, epistemic_status, claim_text, context_path, derivation |
| **D** | Agenda resolver (identity + ontology reuse + necessity-gated proposals) |
| **E** | Clarification planner + `continue_ingestion` (+ resume from `paused`) |
| **F** | Component commit recipes + evidence + effect resume |
| **G** | MCP tools + dogfood on strategy package |

---

## Test plan (must-pass)

1. Hypothesis Claim never appears as effective fact about subject under default search.
2. Default search does not return Claim entities via lexical leak; `include_claims=true` does.
3. Direct Claim id lookup returns Claim structure.
4. Rejected hypothesis → `claimStatus=rejected`, polarity unchanged; no domain triple.
5. Negative recommendation → polarity negative, status active; no positive domain fact.
6. Negative factual assertion → Claim(`epistemicKind=assertion`), not domain Statement; interior not asserted.
7. `claimPredicateKey` never causes domain predicate assert.
8. Two sources, same proposition → two Claims; rejecting one does not reject the other.
9. `claimText`-only question (no SPO) commits successfully.
10. `epistemic_status` from extraction drives `claimStatus`; path alone does not.
11. Idempotent ingest replay.
12. Effect resume: multiple `assert_statement` effect_keys per candidate; retry evidence only.
13. Budget exhaustion → `paused`; `continue_ingestion` without answers resumes.
14. Dry-run: nothing persisted; cannot resume dry-run clarification into execute.
15. Independent component commits while another remains blocked.
16. Unknown phrase does not auto-create ontology without necessity.
17. Ordinary positive assertion still uses Statement path (no Claim).
18. MQ warnings can appear on commit responses.

---

## Explicitly out of V1

- Claim promotion to domain facts
- Proposition-level Claim clustering / global Claim identity
- Separate speech-act predicates
- Auto-apply ontology
- `assert_batch` commit path
- Giant atomic multi-effect DB transactions
- Async worker requirement
- `attributedTo` speaker model
- Broad modal KR
- UI

---

## Related documents

- [architecture.md](architecture.md)
- [design-thesis.md](design-thesis.md)
- [memory-quality-v1.md](memory-quality-v1.md)
- [mcp-contract.md](mcp-contract.md)
- [invariants.md](invariants.md)
- [roadmap.md](roadmap.md) — Milestone H
