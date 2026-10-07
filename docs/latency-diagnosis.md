# Read latency diagnosis (perf/read-latency-diagnosis)

Diagnosis-first measurements against production LAN API
`http://10.10.0.12:5060` (2026-10-07). No broad optimizations applied yet.

## Latency budget (representative)

### `search_entities("Didac")`

| Layer | Measured |
| --- | ---: |
| Direct LAN HTTP client wall (p50 / p95) | **37.5 / 56.4 ms** |
| ChatGPT → MCP observed (reported) | **~2580 ms** |
| **Implied external overhead** | **~2500 ms** |

AtlasSynapse service for exact entity search is already in the excellent band
(&lt;100 ms). The multi-second floor seen from ChatGPT is **not** PostgreSQL or
Pi hardware for this path.

### `get_entity_neighborhood(limit=50)` / `get_relevant_context(limit=25)`

| Operation | LAN p50 (before) | LAN p50 (after batching) | Notes |
| --- | ---: | ---: | --- |
| neighborhood | 699 ms | **179 ms** | N+1 eliminated (`perf/neighborhood-batching`) |
| relevant_context | 1032 ms | **338 ms** | Nested neighborhood win |
| ChatGPT neighborhood | ~3120 ms | — | still ~server + ~2–2.5 s external floor |
| ChatGPT relevant_context | ~4040 ms | — | same pattern |

## Phase 2 — direct LAN HTTP (bypass ChatGPT/MCP)

Warm-up 1 + n=20 measured requests:

```text
search_entities Didac:   p50=37.5ms  p90=53.9ms  p95=56.4ms  max=68.4ms   ~5 KB
search_entities Airbus:  p50=37.4ms  p90=46.9ms  p95=47.5ms  max=53.2ms   ~4 KB
get_entity Didac:        p50=13.5ms  p90=19.3ms  p95=22.5ms  max=22.5ms   ~0.5 KB
neighborhood limit=50:   p50=698.8ms p90=856.9ms p95=1028.8ms max=1077ms  ~69 KB
relevant_context lim=25: p50=1031.9ms p90=1379.7ms p95=1467.9ms max=1478ms ~201 KB
```

Script: `scripts/bench_read_latency.py`.

## Phase 3 — concurrency

### HTTP (uvicorn, single process, sync handlers in threadpool)

```text
sequential wall ≈ 2905 ms
concurrent wall ≈ 2861 ms   (5 reads started together)
```

Requests **do start concurrently**. Individual times inflate under contention
(relevant_context ~1.4 s alone → ~2.9 s in the batch). Wall time tracks the
slowest concurrent request, not a clean 5× sum — HTTP is not globally
single-threaded, but heavy reads share CPU/DB.

### MCP (stdio transport) — implementation finding

**stdio / JSON-RPC do not inherently require serial execution.** Request IDs can
correlate multiple in-flight calls if the server dispatches concurrently and
synchronizes stdout writes.

**The current AtlasSynapse `StdioMCPServer.run()` implementation is serial**:

```text
read JSON-RPC → dispatch tools/call → write response → next message
```

There is **no concurrent `tools/call` handling in this process today**. A ChatGPT
batch of 5 “concurrent” MCP reads **must queue** in this implementation
(tunnel→stdio pipe into a single-flight loop).

Expected lower bound for that batch under the current server ≈ sum of per-call
(handler + ChatGPT/tunnel overhead), not ≈ max(handler).

Observed ~67 s wall with all finishing together is consistent with:

1. serial dispatch in *our* MCP server loop, plus
2. ~2–4 s external overhead **per** call, plus
3. possible ChatGPT-side scheduling / retries / catalog chatter

not with PostgreSQL locking five reads for a minute.

This is potentially fixable (concurrent dispatch + serialized writes) after
checking whether an upstream MCP server library already provides it.

## Phase 4 — inspected bottlenecks

### Database lifecycle — OK

- Process-wide engine via `configure_engine()` at API/MCP startup
- `QueuePool`, `DATABASE_POOL_SIZE=5`, `DATABASE_MAX_OVERFLOW=10`, `pool_pre_ping=True`
- Not creating/disposing engine per request
- MCP opens a **short-lived Session per tool call** (fine; uses the shared pool)

### Async/sync

- FastAPI routes are sync `def` → run in Starlette threadpool
- MCP handlers are sync and today run one-at-a-time on the stdio read loop
- Auth middleware is cheap `hmac.compare_digest` (no remote calls, no disk reload)

### Locks

- No global read lock on retrieval
- `threading.Lock` only in review metrics (write-path adjacent)

### Worker / server model

| Process | Model |
| --- | --- |
| API | `uvicorn` factory, **1 worker**, no `--workers` |
| MCP | `semantic-memory-mcp` stdio, **1 process; current loop is single-flight** |
| Tunnel | long-lived `tunnel-client` → `docker compose run --rm -T mcp` |

### Serialization / size

- search_entities ~5 KB — cannot explain 2.5 s
- relevant_context ~200 KB — serialization is secondary to assembly cost

### Tunnel / proxy path

```text
ChatGPT → OpenAI Secure MCP Tunnel → tunnel-client (Satellite)
        → docker compose run mcp (stdio JSON-RPC)
        → RetrievalService → SQLAlchemy → Postgres
```

No nginx in front of MCP. API LAN path is direct to uvicorn on `:5060`.
Reconnect of ChatGPT does **not** restart the MCP container (known deploy trap).

## Phase 5 — SQL

Skipped deep index tuning for `search_entities`: LAN p50 **37 ms** already.
Neighborhood/relevant_context spend time in **Python N+1 assemble/scoring**
(per-edge `_score_statement`, `resolve_survivor_id`, `statement_service.get`),
not a missing primary-key lookup.

## Ranked diagnosis (likely impact)

1. **ChatGPT / Secure MCP Tunnel / client overhead (~2–2.5 s floor on tiny reads)**  
   Evidence: LAN search 37 ms vs ChatGPT ~2580 ms for the same logical op.

2. **Current MCP stdio server serializes tool calls (implementation, not protocol)**  
   Evidence: `StdioMCPServer.run()` single-flight loop; explains concurrent batch
   wall ≫ max(single) when tools fan out over MCP.

3. **Neighborhood / relevant_context server cost (0.7–1.5 s)**  
   Evidence: LAN benchmarks; N+1 scoring/assemble loop. Real, but smaller than (1)
   for simple searches and additive for graph reads (~25% of ChatGPT
   `get_relevant_context` wall).

4. **Single uvicorn worker + DB pool contention under parallel heavy reads**  
   Evidence: concurrent HTTP inflates per-request times; secondary.

5. **Not the bottleneck:** Pi5 generically, Postgres connectivity, auth crypto,
   engine-per-request, global read mutex, response JSON for small searches,
   “stdio inherently serial.”

## Verdict: local hosting is not too slow

**Closed.** Evidence is decisive for single-call reads:

```text
HTTP LAN                         ~54 ms
local MCP stdio                  ~40 ms
Secure Tunnel enqueue→response  ~255 ms   (tunnel-client metrics)
ChatGPT observed               ~2580 ms
```

Roughly **90% of end-to-end latency is above the tunnel**, not in AtlasSynapse,
the Pi, Postgres, or MCP stdio. Stop chasing the ChatGPT ~2.5 s feel inside
this service. Detail: `docs/mcp-path-benchmark.md`.

**Latency project closed.** Remaining work is agent interaction design, not hosting speed:

1. ~~Concurrent MCP dispatch~~ — `perf/mcp-concurrent-dispatch`
2. ~~Neighborhood N+1 batching~~ — `perf/neighborhood-batching` (LAN p50 179 / 338 ms)
3. ~~Mutation result envelopes~~ — `feat/mutation-result-context` (`return_mode`, effective_state)
4. Next arch question: **MCP tool-surface simplification** (ChatGPT default vs admin tools)

## Smallest proposed fixes (status)

1. ~~Benchmark local MCP vs tunnel MCP vs ChatGPT MCP~~ — done.
2. ~~Concurrent MCP dispatch~~ — done.
3. ~~Remove neighborhood N+1~~ — done. **No further low-level read latency work**
   unless fresh measurements justify it.
4. Prefer aggregate tools (`get_relevant_context`) to amortize connector latency.
5. Uvicorn workers / DB tuning — not justified.
6. ~~Mutation `standard` envelopes~~ — done (`feat/mutation-result-context`).

## Instrumentation added on this branch

- `RequestTimer` stages on `search_entities`, `get_entity_neighborhood`,
  `get_relevant_context` (structured `read_timing` logs)
- HTTP `X-Atlas-Server-Ms` always; `X-Atlas-Timings` when `X-Debug-Timings: 1`
- MCP `mcp_tool_timing` log per `tools/call` (handler vs serialization)
- `scripts/bench_read_latency.py` for repeatable LAN benches

`get_relevant_context` also returns `metadata.timings_ms` for coarse agent-visible
budgets (debug-friendly; not a stable public contract).
