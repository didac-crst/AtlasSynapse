# MCP path benchmark (`perf/mcp-path-benchmark`)

Goal: locate the ~2.5 s ChatGPT floor for a trivial read by comparing transports
for the same call: `search_entities("Didac")`.

Script: `scripts/bench_mcp_path.py`

## Measured (2026-10-07, Satellite)

| # | Path | p50 / notes |
| --- | --- | ---: |
| 1 | HTTP LAN API | **53.6 ms** (n=20) |
| 2 | Local MCP stdio (separate `compose run`) | **39.5 ms** (n=20) |
| 3 | Secure Tunnel MCP (`tunnel-client` `enqueue_to_response` for `tools/call`) | **~255 ms** avg (n=1 historical sample in metrics) |
| 3b | Tunnel `poll_to_response` | **~184 ms** avg (same sample) |
| 4 | ChatGPT → Secure MCP Tunnel (earlier observation) | **~2580 ms** |

Local MCP boot cost (~7.5 s for a fresh `docker compose run`) is **excluded** from
samples (warmup after initialize).

## Decomposition

```text
HTTP LAN                         ~54 ms
local MCP stdio                  ~40 ms   (≈ HTTP; stdio overhead ≈ 0)
Secure Tunnel enqueue→response  ~255 ms   (includes MCP handler + tunnel RTT)
ChatGPT observed               ~2580 ms
────────────────────────────────────────
ChatGPT − tunnel E2E           ~2325 ms   ← outside AtlasSynapse + tunnel-client
tunnel − local MCP             ~215 ms    ← control-plane/tunnel path (small)
local MCP − HTTP                 ~0 ms
```

## Conclusion

- **AtlasSynapse retrieval for this call is fine** (~40–55 ms).
- **Local MCP adds negligible overhead** vs HTTP.
- **Secure Tunnel path is hundreds of ms, not seconds** (metrics sample ~255 ms
  enqueue→response). Worth watching with more `tools/call` samples after ChatGPT
  traffic, but it does **not** explain a 2.5 s floor.
- **~2.3 s of the ChatGPT observation is above the tunnel** — ChatGPT / OpenAI
  connector orchestration. Stop optimizing AtlasSynapse or the Pi for that floor.
- Moving AtlasSynapse off the Pi 5 for performance is **not supported** by this data.

## Method notes

- Tunnel cannot be client-driven from Satellite (inbound from OpenAI control plane).
  Layer 3 uses `tunnel-client` Prometheus metrics at `http://127.0.0.1:8080/metrics`.
- Re-run after ChatGPT traffic to grow `tools/call` histogram counts:

```sh
set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
python3 scripts/bench_mcp_path.py --chatgpt-ms 2580
```

## Next branches (unchanged plan)

1. ~~`perf/mcp-path-benchmark`~~ (this)
2. `perf/mcp-concurrent-dispatch` — multi in-flight JSON-RPC (separate experiment)
3. `perf/neighborhood-batching` — N+1 removal (~700 ms → &lt;300 ms)
