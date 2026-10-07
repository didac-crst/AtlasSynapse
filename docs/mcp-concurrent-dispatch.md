# MCP concurrent dispatch (`perf/mcp-concurrent-dispatch`)

## Change

`StdioMCPServer` can dispatch multiple `tools/call` handlers concurrently
(`MCP_MAX_INFLIGHT`, default **8**). Stdout writes remain under a lock; responses
are correlated by JSON-RPC `id` (order may differ from request order).

Lifecycle methods (`initialize`, `tools/list`, `ping`) stay on the reader thread.
`MCP_MAX_INFLIGHT=1` restores single-flight behavior for A/B benches.

## Correctness

Unit tests (`tests/test_mcp_server.py`):

- five 150 ms tools with `max_inflight=5`: `max_active >= 2`, wall ≪ 5×150 ms,
  all JSON-RPC ids returned
- `max_inflight=1`: `max_active == 1` (single-flight restored)

## Benchmark (batch wall, not single-call latency)

```sh
set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
python3 scripts/bench_mcp_concurrent.py --rounds 5
# optional host stdio (no docker):
python3 scripts/bench_mcp_concurrent.py --local --rounds 5
```

Key metric: wall time for 2 / 5 / 10 simultaneous `search_entities` with id
correctness. Concurrent success looks like wall ≈ slowest call, not ≈ N × single.

### Measured (2026-10-07, local host stdio, 3 rounds)

| Batch | inflight=1 wall p50 | inflight=8 wall p50 | Notes |
| ---: | ---: | ---: | --- |
| 2 | 95 ms | 70 ms | ids ok |
| 5 | 270 ms | 130 ms | ~2× wall cut |
| 10 | 487 ms | 278 ms | ~1.75× wall cut |

All JSON-RPC ids returned non-error results. Concurrent dispatch removes
artificial single-flight queueing: batch wall moves toward max(call) rather
than sum(call). Unit tests prove handler overlap (`max_active >= 2`).

Docker compose MCP of identical hot `search_entities` can still look closer to
serial when DB/pool contention dominates; use `--local` for a clearer A/B of
the dispatch change itself.

## Relation to ChatGPT floor

This does **not** remove the ~2.5 s ChatGPT orchestration floor on a single call
(~90% of that budget is above the tunnel; see `docs/latency-diagnosis.md`).
It removes artificial single-flight queueing when an agent issues multiple MCP
reads at once — the remaining architectural issue under our control for agent
workflows.
