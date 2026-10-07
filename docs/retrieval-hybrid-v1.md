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
python3 scripts/bench_retrieval_quality.py --out benchmarks/retrieval/v1/temporal_v1_report.json
```

Metrics: Recall@1 / @3 / @10, MRR, zero-hit rate, superseded-in-topk rate, plus by-tag class breakdown.

## Baseline (`f82ecad`, whole-string ILIKE)

Report: `benchmarks/retrieval/v1/baseline_report.json`

| Metric | Value |
| --- | ---: |
| Recall@1 / @3 / @10 | **22.5%** |
| Zero-hit rate | **75.0%** |
| Multi-token zeros | **13/13** |

## Checkpoint: tokenized lexical (`ab49f57`)

Report: [`benchmarks/retrieval/v1/lexical_v1_report.json`](../benchmarks/retrieval/v1/lexical_v1_report.json)

| Metric | Baseline | Lexical v1 |
| --- | ---: | ---: |
| Recall@1 | 22.5% | **92.5%** |
| Recall@3 | 22.5% | **97.5%** |
| Recall@10 | 22.5% | **97.5%** |
| Zero-hit rate | 75.0% | **0.0%** |
| MRR | 1.0 | 0.944 |

Most failures were candidate-generation brittleness, not missing graph data.

## Checkpoint: temporal / effective-state (`temporal_v1`)

**Still no entity anchoring / predicate intent / graph expansion.**

Added:

- `detect_temporal_intent` (`current` / `historical` / `neutral`) from raw query cues
- Effective-state ranking: open-ended asserted facts outrank unbounded descriptive rows for current/default
- Historical intent: include superseded pass, boost legacy descriptions / start-date beliefs, demote currently effective rows
- Sparse exact-name penalty on long multi-term queries (lone `Airbus` name must not bury denser object text)

Report: [`benchmarks/retrieval/v1/temporal_v1_report.json`](../benchmarks/retrieval/v1/temporal_v1_report.json)

### Overall delta

| Metric | Lexical v1 | Temporal v1 |
| --- | ---: | ---: |
| Recall@1 | 92.5% | **97.5%** |
| Recall@3 | 97.5% | **100%** |
| Recall@10 | 97.5% | **100%** |
| Zero-hit | 0% | **0%** |
| MRR | 0.944 | **0.988** |
| Historical class R@1 | 75% | **100%** |
| Historical class R@3 | 75% | **100%** |

### Notable behavior

- `Didac current Airbus role` → gold `holdsRole` still R@1 with `effective_open_end`
- `What did AtlasSynapse previously think the Airbus role start date was?` → gold legacy description (`4206a351…`, started 1 Jan 2026) now **R@1**
- Default/current search stays **asserted-only**; historical adds a superseded candidate pass

## Checkpoint: bounded 1-hop anchor expansion (`anchor_v1`)

**Historical-only.** Current/default paths skip expansion (avoids adjacency noise regressions).

```text
strong entity anchors (≤3, exact-name / high lexical)
  → entity-valued 1-hop statements only
  → asserted + superseded when historical
  → same scorer + dedupe with lexical hits
```

Report: [`benchmarks/retrieval/v1/anchor_v1_report.json`](../benchmarks/retrieval/v1/anchor_v1_report.json)

| Metric | Temporal v1 | Anchor v1 |
| --- | ---: | ---: |
| Recall@1 | 97.5% | **97.5%** |
| Recall@3 | 100% | **100%** |
| MRR | 0.988 | **0.988** |
| Zero-hit | 0% | **0%** |

### What it unlocks

- `Didac previous Airbus role` / `What was Didac doing before this role?` → structured superseded `holdsRole` (`aa10530b…`) at **R@1** via `anchor_1hop`
- `Didac current Airbus role` → no superseded leakage; asserted-only path unchanged
- Latency: historical ~350ms LAN; current ~140ms (expansion skipped)

### Remaining gaps

- Historical superseded boost currently promotes *all* superseded entity-valued neighbors equally (holdsRole and hasGoal tie) — soft predicate intent would separate them
- `when-did-didac-start-current-role` still has mild competition from non-role adjacency on some phrasings
- Embeddings still not justified

## Implementation order

1. ~~Benchmark + baseline~~ (`f82ecad`)
2. ~~Tokenized multi-term lexical matching~~ (`ab49f57`)
3. ~~Temporal intent / effective-state + legacy demotion~~ (`a7a7123`)
4. ~~Bounded 1-hop anchor expansion (historical-only)~~ (this checkpoint)
5. Soft predicate intent — only if role-vs-goal historical ties matter in practice
6. Embeddings only after measuring remaining gaps

Principle:

> Use lexical text to understand what the user *mentions*;
> use ontology + graph + time to retrieve what the user *means*.
