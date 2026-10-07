#!/usr/bin/env python3
"""Decompose search_entities latency across transport layers.

Measures the same trivial call through:

  1. HTTP LAN API
  2. Local MCP stdio (separate docker compose run; not the tunnel session)

Cannot client-drive OpenAI Secure MCP Tunnel from Satellite (inbound from
control plane only). ChatGPT MCP numbers are accepted as the remote upper
bound; optional --chatgpt-ms records them beside local measurements.

Usage:
  set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
  python scripts/bench_mcp_path.py \\
    --base http://10.10.0.12:5060 \\
    --chatgpt-ms 2580
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.satellite.yml"
QUERY = "Didac"
TOOL = "search_entities"


def _pct(samples: list[float], p: float) -> float:
    samples = sorted(samples)
    i = min(len(samples) - 1, max(0, int(round((p / 100) * (len(samples) - 1)))))
    return samples[i]


def _summary(label: str, samples: list[float], *, extra: str = "") -> None:
    print(
        f"{label}: n={len(samples)} "
        f"p50={_pct(samples, 50):.1f}ms p90={_pct(samples, 90):.1f}ms "
        f"p95={_pct(samples, 95):.1f}ms max={max(samples):.1f}ms "
        f"mean={statistics.mean(samples):.1f}ms{extra}"
    )


def bench_http(base: str, token: str, n: int, warmup: int) -> list[float]:
    path = "/v1/entities/search?" + urllib.parse.urlencode({"query": QUERY, "limit": 25})

    def once() -> float:
        req = urllib.request.Request(
            base.rstrip("/") + path,
            headers={"Authorization": f"Bearer {token}"},
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            status = resp.status
            server = resp.headers.get("X-Atlas-Server-Ms")
        assert status == 200, status
        assert raw
        _ = server
        return (time.perf_counter() - t0) * 1000

    for _ in range(warmup):
        once()
    return [once() for _ in range(n)]


class LocalMcpSession:
    """Long-lived local MCP stdio child (separate from tunnel mcp-run)."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE),
                "--profile",
                "mcp",
                "run",
                "--rm",
                "-T",
                "--name",
                f"atlas-synapse-mcp-bench-{os.getpid()}",
                "mcp",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(COMPOSE.parent),
        )
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self._next_id = 1
        # initialize + notifications/initialized
        init = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bench_mcp_path", "version": "0.1"},
            },
        )
        assert "result" in init, init
        self._notify("notifications/initialized", {})

    def close(self) -> None:
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def _write(self, message: dict) -> None:
        assert self.proc.stdin is not None
        line = json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
        self.proc.stdin.write(line.encode("utf-8"))
        self.proc.stdin.flush()

    def _read(self) -> dict:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if line == b"":
                err = b""
                if self.proc.stderr:
                    err = self.proc.stderr.read()
                raise RuntimeError(f"MCP stdout closed; stderr={err.decode()[:500]}")
            stripped = line.strip()
            if not stripped:
                continue
            return json.loads(stripped.decode("utf-8"))

    def _rpc(self, method: str, params: dict) -> dict:
        msg_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        while True:
            msg = self._read()
            if msg.get("id") == msg_id:
                return msg

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def call_search_entities(self) -> tuple[float, int]:
        t0 = time.perf_counter()
        resp = self._rpc(
            "tools/call",
            {"name": TOOL, "arguments": {"payload": {"query": QUERY, "limit": 25}}},
        )
        elapsed = (time.perf_counter() - t0) * 1000
        if "error" in resp:
            raise RuntimeError(resp)
        result = resp.get("result") or {}
        text = ""
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "")
                break
        return elapsed, len(text.encode("utf-8"))


def bench_local_mcp(n: int, warmup: int) -> list[float]:
    print("starting local MCP stdio session (docker compose run)…", flush=True)
    t_boot = time.perf_counter()
    session = LocalMcpSession()
    boot_ms = (time.perf_counter() - t_boot) * 1000
    print(f"local MCP ready after {boot_ms:.0f}ms (excluded from samples)", flush=True)
    try:
        for _ in range(warmup):
            session.call_search_entities()
        samples: list[float] = []
        for _ in range(n):
            ms, _nbytes = session.call_search_entities()
            samples.append(ms)
        return samples
    finally:
        session.close()


def scrape_tunnel_tools_call_ms(metrics_url: str) -> dict[str, float | None]:
    """Read tunnel-client histogram sums for tools/call if the daemon is up."""
    import re
    import urllib.error

    out: dict[str, float | None] = {
        "enqueue_to_response_avg_ms": None,
        "poll_to_response_avg_ms": None,
        "tools_call_count": None,
    }
    try:
        with urllib.request.urlopen(metrics_url, timeout=5) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return out

    sums: dict[str, float] = {}
    counts: dict[str, float] = {}
    for kind, labels, value in re.findall(
        r"command_end_to_end_latency_milliseconds_(sum|count)\{([^}]*)\} ([0-9.]+)",
        text,
    ):
        if 'request_method="tools/call"' not in labels:
            continue
        m = re.search(r'latency_type="([^"]+)"', labels)
        if not m:
            continue
        key = m.group(1)
        if kind == "sum":
            sums[key] = float(value)
        else:
            counts[key] = float(value)
    if counts.get("enqueue_to_response"):
        out["tools_call_count"] = counts["enqueue_to_response"]
        out["enqueue_to_response_avg_ms"] = (
            sums.get("enqueue_to_response", 0.0) / counts["enqueue_to_response"]
        )
    if counts.get("poll_to_response"):
        out["poll_to_response_avg_ms"] = (
            sums.get("poll_to_response", 0.0) / counts["poll_to_response"]
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://10.10.0.12:5060")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument(
        "--chatgpt-ms",
        type=float,
        default=None,
        help="Observed ChatGPT→MCP wall ms for search_entities(Didac)",
    )
    parser.add_argument(
        "--tunnel-metrics",
        default="http://127.0.0.1:8080/metrics",
        help="tunnel-client Prometheus metrics URL (enqueue_to_response)",
    )
    args = parser.parse_args()
    token = os.environ.get("HTTP_API_TOKEN")
    if not token:
        raise SystemExit("HTTP_API_TOKEN required")

    print(f"=== path benchmark: {TOOL}({QUERY!r}) n={args.n} ===\n")

    http = bench_http(args.base, token, args.n, args.warmup)
    _summary("1 HTTP LAN", http)

    local = bench_local_mcp(args.n, args.warmup)
    _summary("2 local MCP stdio", local)

    tunnel = scrape_tunnel_tools_call_ms(args.tunnel_metrics)

    http_p50 = _pct(http, 50)
    local_p50 = _pct(local, 50)
    mcp_overhead = local_p50 - http_p50

    print("\n=== decomposition ===")
    print(f"HTTP LAN p50:              {http_p50:8.1f} ms")
    print(f"local MCP stdio p50:       {local_p50:8.1f} ms")
    print(f"local MCP − HTTP:          {mcp_overhead:8.1f} ms  (stdio+JSON-RPC; often ≈0)")
    if tunnel["enqueue_to_response_avg_ms"] is not None:
        print(
            f"3 Secure Tunnel MCP avg:   {tunnel['enqueue_to_response_avg_ms']:8.1f} ms"
            f"  (tunnel-client enqueue_to_response, n={int(tunnel['tools_call_count'] or 0)})"
        )
        if tunnel["poll_to_response_avg_ms"] is not None:
            print(
                f"   poll_to_response avg:   {tunnel['poll_to_response_avg_ms']:8.1f} ms"
            )
        print(
            f"   tunnel − local MCP:     "
            f"{tunnel['enqueue_to_response_avg_ms'] - local_p50:8.1f} ms"
        )
    else:
        print("3 Secure Tunnel MCP:      (metrics unavailable — is tunnel-client up?)")

    if args.chatgpt_ms is not None:
        chatgpt = args.chatgpt_ms
        print(f"4 ChatGPT MCP (reported):  {chatgpt:8.1f} ms")
        print(f"   ChatGPT − local MCP:    {chatgpt - local_p50:8.1f} ms")
        if tunnel["enqueue_to_response_avg_ms"] is not None:
            print(
                f"   ChatGPT − tunnel E2E:   "
                f"{chatgpt - tunnel['enqueue_to_response_avg_ms']:8.1f} ms"
            )
        print(
            "\nInterpretation: if local MCP and tunnel enqueue_to_response stay in "
            "tens/low-hundreds of ms while ChatGPT is ~2.5 s, the fixed floor is "
            "ChatGPT/OpenAI connector orchestration — stop chasing it in AtlasSynapse."
        )
    else:
        print("\nPass --chatgpt-ms <ms> to compare against a ChatGPT observation.")


if __name__ == "__main__":
    main()
