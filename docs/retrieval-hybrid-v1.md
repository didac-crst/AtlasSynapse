# Retrieval hybrid v1 — benchmark first

Branch: `feat/retrieval-hybrid-v1`

## Diagnosis (confirmed)

Structured truth already exists:

```text
Didac ─holdsRole→ Quality Engineering End-to-End Analytics Manager
  valid_from = 2025-09-01, valid_to = null, asserted

Role ─roleAt→ Airbus
```

Prior `holdsRole` with `valid_from=2026-01-01` is correctly **superseded**.

## Gold set

- Cases: [`benchmarks/retrieval/v1/cases.json`](../benchmarks/retrieval/v1/cases.json) (**40** queries)
- Runner: [`scripts/bench_retrieval_quality.py`](../scripts/bench_retrieval_quality.py)

```sh
set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
python3 scripts/bench_retrieval_quality.py --limit 25
python3 scripts/bench_retrieval_quality.py --out benchmarks/retrieval/v1/lexical_v1_report.json
```

Metrics: Recall@1 / @3 / @10, MRR, zero-hit rate, superseded-in-topk rate, plus by-tag class breakdown.

## Baseline (`f82ecad`, whole-string ILIKE)

LAN `10.10.0.12:5060`, 2026-10-07 — report: `benchmarks/retrieval/v1/baseline_report.json`

| Metric | Value |
| --- | ---: |
| Cases | 40 |
| Recall@1 | **22.5%** (9/40) |
| Recall@3 | **22.5%** (9/40) |
| Recall@10 | **22.5%** (9/40) |
| MRR (when any hit) | 1.0 |
| Zero-hit rate | **75.0%** (30/40) |
| Multi-token/concept zeros | **13/13** |
| Tagged `lexical_ok` Recall@3 | 9/11 |

Diagnostic zeros: `Didac current Airbus role`, `Didac Airbus`.

## Checkpoint: tokenized lexical (`lexical_v1`)

**No entity anchoring / temporal intent / predicate intent / graph expansion yet.**

Candidate generation now:

- punctuation-insensitive tokenization
- stopword drop (questions + temporal fillers reserved for later stages)
- OR of content-token ILIKE patterns
- statements also match **subject/object entity names** (entity-valued rows often have empty `object_string`)
- ranking keeps exact-name boosts; partial single-token-of-many stays mediocre

Report: [`benchmarks/retrieval/v1/lexical_v1_report.json`](../benchmarks/retrieval/v1/lexical_v1_report.json)

### Overall delta vs `f82ecad`

| Metric | Baseline | Lexical v1 |
| --- | ---: | ---: |
| Recall@1 | 22.5% | **92.5%** |
| Recall@3 | 22.5% | **97.5%** |
| Recall@10 | 22.5% | **97.5%** |
| Zero-hit rate | 75.0% | **0.0%** |
| Multi-token/concept zeros | 13/13 | **0/13** |
| Diagnostic zeros | 2/2 | **0/2** |
| MRR | 1.0 | 0.944 |

### By query class (lexical v1)

| Class | n | R@1 | R@3 | R@10 | zero |
| --- | ---: | ---: | ---: | ---: | ---: |
| diagnostic | 2 | 100% | 100% | 100% | 0% |
| multi_token | 10 | 100% | 100% | 100% | 0% |
| multi_concept | 3 | 100% | 100% | 100% | 0% |
| lexical_ok | 11 | 90.9% | 100% | 100% | 0% |
| temporal | 8 | 100% | 100% | 100% | 0% |
| predicate_intent | 11 | 100% | 100% | 100% | 0% |
| graph | 8 | 100% | 100% | 100% | 0% |
| family | 4 | 100% | 100% | 100% | 0% |
| historical | 4 | 75% | 75% | 75% | 0% |
| quality | 3 | 66.7% | 100% | 100% | 0% |
| entity | 8 | 100% | 100% | 100% | 0% |
| goal | 2 | 100% | 100% | 100% | 0% |

### What this proves

Most of the 75% zero-hit rate was **brittle full-string matching**, not missing graph data.

`Who is Didac?`, `Didac Airbus`, and `Didac current Airbus role` now produce candidates. For the diagnostic role query, gold `holdsRole` (`d315de3e…`) is currently rank 1 because subject-name token match surfaces Didac-linked statements.

### What this does **not** solve yet

- **Noise:** many Didac-adjacent statements (`hasParticipant`, other `holdsRole`) sit near the gold hit with similar scores.
- **Composition:** Airbus + role + current is still mostly accidental co-occurrence via OR tokens + name joins, not entity anchoring / predicate intent / temporal filtering.
- **Historical:** `previous-role-start-date-belief` still MISS (relevant at R@11) — superseded / legacy text needs first-class historical intent.
- **Effective vs superseded ranking** is not deliberate yet (defaults already exclude superseded status from search, but legacy descriptive duplicates remain).

## Implementation order

1. ~~Benchmark + baseline~~ (`f82ecad`)
2. ~~Tokenized multi-term lexical matching~~ (this checkpoint)
3. Entity anchoring
4. Temporal intent / effective-state boost
5. Predicate / ontology intent
6. Bounded 1-hop graph expansion
7. Embeddings only after measuring remaining gaps

Principle:

> Use lexical text to understand what the user *mentions*;
> use ontology + graph + time to retrieve what the user *means*.
