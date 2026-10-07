#!/usr/bin/env python3
"""LAN HTTP read-latency benchmark for AtlasSynapse (diagnosis aid).

Usage:
  set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
  python scripts/bench_read_latency.py --base http://10.10.0.12:5060
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def _request(
    base: str, token: str, method: str, path: str, body: dict | None = None, debug: bool = False
) -> tuple[int, float, int, dict[str, str], bytes]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if debug:
        headers["X-Debug-Timings"] = "1"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        status = resp.status
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    return status, (time.perf_counter() - t0) * 1000, len(raw), resp_headers, raw


def _pct(samples: list[float], p: float) -> float:
    samples = sorted(samples)
    i = min(len(samples) - 1, max(0, int(round((p / 100) * (len(samples) - 1)))))
    return samples[i]


def bench(label: str, fn, n: int = 20, warmup: int = 1) -> list[float]:
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    sizes: list[int] = []
    server_ms: list[float] = []
    for _ in range(n):
        status, ms, size, headers, _ = fn()
        assert status == 200, (label, status)
        samples.append(ms)
        sizes.append(size)
        if "x-atlas-server-ms" in headers:
            server_ms.append(float(headers["x-atlas-server-ms"]))
    print(
        f"{label}: n={n} client_p50={_pct(samples, 50):.1f}ms "
        f"p90={_pct(samples, 90):.1f}ms p95={_pct(samples, 95):.1f}ms "
        f"max={max(samples):.1f}ms bytes~{int(statistics.mean(sizes))}"
        + (
            f" server_p50={_pct(server_ms, 50):.1f}ms"
            if server_ms
            else ""
        )
    )
    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://10.10.0.12:5060")
    parser.add_argument("--entity-id", default="c87bdec9-5757-4278-ae88-d5fe291a2b36")
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()
    token = os.environ["HTTP_API_TOKEN"]
    base = args.base.rstrip("/")
    didac = args.entity_id

    def get(path: str, debug: bool = False):
        return _request(base, token, "GET", path, debug=debug)

    def post(path: str, body: dict, debug: bool = False):
        return _request(base, token, "POST", path, body, debug=debug)

    print("=== LAN HTTP benchmarks ===")
    bench(
        "search_entities Didac",
        lambda: get("/v1/entities/search?" + urllib.parse.urlencode({"query": "Didac"})),
        n=args.n,
    )
    bench("get_entity", lambda: get(f"/v1/entities/{didac}"), n=args.n)
    bench(
        "neighborhood",
        lambda: get(f"/v1/entities/{didac}/neighborhood?limit=50"),
        n=args.n,
    )
    bench(
        "relevant_context",
        lambda: post("/v1/memory/relevant-context", {"entity_id": didac, "limit": 25}),
        n=args.n,
    )

    print("\n=== debug timings (single shot) ===")
    for label, fn in (
        ("search", lambda: get("/v1/entities/search?query=Didac", debug=True)),
        ("neighborhood", lambda: get(f"/v1/entities/{didac}/neighborhood?limit=50", debug=True)),
        (
            "relevant_context",
            lambda: post(
                "/v1/memory/relevant-context",
                {"entity_id": didac, "limit": 25},
                debug=True,
            ),
        ),
    ):
        status, ms, size, headers, _ = fn()
        print(label, f"client={ms:.1f}ms", f"server={headers.get('x-atlas-server-ms')}")
        if "x-atlas-timings" in headers:
            print(" ", headers["x-atlas-timings"])

    print("\n=== sequential vs concurrent ===")
    ops = [
        ("get_entity", lambda: get(f"/v1/entities/{didac}")),
        ("neighborhood", lambda: get(f"/v1/entities/{didac}/neighborhood?limit=50")),
        (
            "relevant_context",
            lambda: post("/v1/memory/relevant-context", {"entity_id": didac, "limit": 25}),
        ),
        ("search_entities", lambda: get("/v1/entities/search?query=Didac")),
        ("search_memory", lambda: get("/v1/memory/search?query=Didac")),
    ]
    t0 = time.perf_counter()
    for name, fn in ops:
        status, ms, size, _, _ = fn()
        print(f"  seq {name}: {ms:.1f}ms")
    seq_wall = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(fn): name for name, fn in ops}
        for fut in as_completed(futs):
            status, ms, size, _, _ = fut.result()
            print(f"  conc {futs[fut]}: {ms:.1f}ms")
    conc_wall = (time.perf_counter() - t0) * 1000
    print(f"sequential_wall={seq_wall:.1f}ms concurrent_wall={conc_wall:.1f}ms")


if __name__ == "__main__":
    main()
