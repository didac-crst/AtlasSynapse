# Retrieval hybrid v1 — benchmark first

Branch: `feat/retrieval-hybrid-v1`

**No retrieval logic changes yet.** This document records the gold set and
baseline metrics for current `search_memory` (`GET /v1/memory/search`).

## Diagnosis (confirmed)

Structured truth already exists:

```text
Didac ─holdsRole→ Quality Engineering End-to-End Analytics Manager
  valid_from = 2025-09-01, valid_to = null, asserted

Role ─roleAt→ Airbus
```

Prior `holdsRole` with `valid_from=2026-01-01` is correctly **superseded**.

Today’s `search_memory` is whole-string `ILIKE %query%` over entity names/aliases
and statement **object text only**. It does not compose multi-entity queries,
temporal intent, or graph hops.

## Gold set

- Cases: [`benchmarks/retrieval/v1/cases.json`](../benchmarks/retrieval/v1/cases.json) (**40** queries)
- Runner: [`scripts/bench_retrieval_quality.py`](../scripts/bench_retrieval_quality.py)

```sh
set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
python3 scripts/bench_retrieval_quality.py --limit 25
```

Metrics: Recall@1 / @3 / @10, MRR, zero-hit rate, superseded-in-topk rate.

## Baseline (LAN `10.10.0.12:5060`, 2026-10-07)

| Metric | Value |
| --- | ---: |
| Cases | 40 |
| Recall@1 | **22.5%** (9/40) |
| Recall@3 | **22.5%** (9/40) |
| Recall@10 | **22.5%** (9/40) |
| MRR (when any hit) | 1.0 |
| Zero-hit rate | **75.0%** (30/40) |
| Superseded-in-topk | 0% (defaults exclude superseded) |
| Multi-token/concept zeros | **13/13** |
| Tagged `lexical_ok` Recall@3 | 9/11 |

### Diagnostic zeros (must fix)

| Query | Hits |
| --- | ---: |
| `Didac current Airbus role` | 0 |
| `Didac Airbus` | 0 |

### Other instructive failures

| Query | Hits | Note |
| --- | ---: | --- |
| `Who is Didac?` | 0 | Full-query ILIKE; `?` / question phrasing kills match |
| `Didac goals` | 0 | Multi-token |
| `role at Airbus` | 0 | Token order / phrasing |
| `Airbus role` | 1 | Hit is a *different* description statement — not gold ids |

### What still works

Exact/single tokens: `Didac`, `Airbus`, `INPG`, `Floriane`, `Siena`, `Elma`,
full role title, long goal title.

## Implementation order (next commits on this branch)

1. ~~Benchmark + baseline~~ (this commit)
2. Tokenized multi-term lexical matching (`Didac Airbus` must not be zero)
3. Entity anchoring
4. Temporal intent / effective-state boost
5. Predicate / ontology intent
6. Bounded 1-hop graph expansion
7. Embeddings only after measuring remaining gaps

Principle:

> Use lexical text to understand what the user *mentions*;
> use ontology + graph + time to retrieve what the user *means*.
