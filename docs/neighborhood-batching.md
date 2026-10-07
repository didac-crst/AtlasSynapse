# Neighborhood batching (`perf/neighborhood-batching`)

## Change

`get_entity_neighborhood` / statement scoring no longer N+1:

- bulk `evidence_stats_for_statements` (count + max reliability)
- bulk `resolve_survivor_ids`
- per-request ontology specificity memo
- `StatementService.to_response` on already-loaded rows
- full DTO build only for the top `limit` edges after sort

Ranking formula and candidate set are unchanged (still score all asserted
timeline edges, then sort + slice).

## Merge gate

`test_neighborhood_batched_ranking_matches_solo_scoring` — batched vs solo
`ranking_score` equality and neighborhood edge order ≡ sort by score.

## Measured (LAN, Didac, n=20, after deploy)

```text
neighborhood     p50=179 ms   (was ~699 ms)
relevant_context p50=338 ms   (was ~1032 ms)
```

Targets met (neighborhood &lt; 300–400, stretch 200–300; context &lt; 600–700).

After this branch: stop low-level read tuning; move to agent interaction
(`feat/mutation-result-context`).
