#!/usr/bin/env python3
"""Benchmark MCP batch wall time under concurrent tools/call.

Compares single-flight (MCP_MAX_INFLIGHT=1) vs concurrent (default 8) for
batches of 2 / 5 / 10 simultaneous search_entities("Didac") calls.
Checks every JSON-RPC id returns a non-error result.

Usage:
  set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
  python scripts/bench_mcp_concurrent.py
  python scripts/bench_mcp_concurrent.py --local
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.satellite.yml"
VENV_MCP = ROOT / ".venv" / "bin" / "semantic-memory-mcp"
QUERY = "Didac"
# Docker-compose DB hostname is not resolvable from the host; published on :2040.
_DOCKER_DB_HOST = "satellite-postgres-timescale"
_HOST_DB_PORT = 2040


def _host_reachable_database_url(url: str) -> str:
    """Rewrite compose DNS hostname to localhost published port for --local."""
    parsed = urlparse(url)
    if parsed.hostname != _DOCKER_DB_HOST:
        return url
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    host = f"127.0.0.1:{_HOST_DB_PORT}"
    return urlunparse(parsed._replace(netloc=f"{userinfo}{host}"))


class ConcurrentMcpSession:
    def __init__(self, *, max_inflight: int, local: bool = False) -> None:
        env = os.environ.copy()
        env["MCP_MAX_INFLIGHT"] = str(max_inflight)
        if local:
            env.setdefault("APP_ENV", "production")
            db = env.get("DATABASE_URL")
            if db:
                env["DATABASE_URL"] = _host_reachable_database_url(db)
            cmd = [str(VENV_MCP)]
            cwd = str(ROOT)
            stderr = subprocess.DEVNULL
        else:
            cmd = [
                "docker",
                "compose",
                "-f",
                str(COMPOSE),
                "--profile",
                "mcp",
                "run",
                "--rm",
                "-T",
                "-e",
                f"MCP_MAX_INFLIGHT={max_inflight}",
                "--name",
                f"atlas-synapse-mcp-cbench-{max_inflight}-{os.getpid()}",
                "mcp",
            ]
            cwd = str(COMPOSE.parent)
            stderr = subprocess.PIPE
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            cwd=cwd,
            env=env,
        )
        assert self.proc.stdin and self.proc.stdout
        self._next_id = 1
        init = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bench_mcp_concurrent", "version": "0.1"},
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
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def _write(self, message: dict) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(
            (json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        )
        self.proc.stdin.flush()

    def _read(self) -> dict:
        assert self.proc.stdout
        while True:
            line = self.proc.stdout.readline()
            if line == b"":
                err = self.proc.stderr.read().decode()[:800] if self.proc.stderr else ""
                raise RuntimeError(f"MCP closed; stderr={err}")
            if line.strip():
                return json.loads(line.decode())

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

    def batch_search(self, n: int) -> tuple[float, list[int]]:
        """Fire n tools/call then collect n responses by id. Return wall ms + ids."""
        ids = list(range(self._next_id, self._next_id + n))
        self._next_id += n
        t0 = time.perf_counter()
        for msg_id in ids:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "method": "tools/call",
                    "params": {
                        "name": "search_entities",
                        "arguments": {"payload": {"query": QUERY, "limit": 10}},
                    },
                }
            )
        got: dict[int, dict] = {}
        while len(got) < n:
            msg = self._read()
            rid = msg.get("id")
            if rid in ids:
                got[int(rid)] = msg
        wall = (time.perf_counter() - t0) * 1000
        for msg_id in ids:
            assert "result" in got[msg_id], got[msg_id]
            result = got[msg_id]["result"]
            assert result.get("isError") is not True, result
        return wall, ids


def bench_mode(
    max_inflight: int, batches: list[int], rounds: int, *, local: bool
) -> None:
    label = "local host" if local else "docker"
    print(f"\n=== MCP_MAX_INFLIGHT={max_inflight} ({label}) ===")
    session = ConcurrentMcpSession(max_inflight=max_inflight, local=local)
    try:
        # warmup
        session.batch_search(1)
        for n in batches:
            samples: list[float] = []
            for _ in range(rounds):
                wall, ids = session.batch_search(n)
                samples.append(wall)
                assert len(ids) == n
            samples.sort()
            p50 = samples[len(samples) // 2]
            print(
                f"batch={n}: rounds={rounds} wall_p50={p50:.1f}ms "
                f"min={min(samples):.1f}ms max={max(samples):.1f}ms ids_ok"
            )
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--batches", default="2,5,10")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use .venv semantic-memory-mcp instead of docker compose",
    )
    args = parser.parse_args()
    batches = [int(x) for x in args.batches.split(",") if x.strip()]
    mode = "local host stdio" if args.local else "docker compose mcp"
    print(f"MCP concurrent dispatch benchmark (search_entities Didac, {mode})")
    bench_mode(1, batches, args.rounds, local=args.local)
    bench_mode(8, batches, args.rounds, local=args.local)
    print(
        "\nSuccess: all JSON-RPC ids return; concurrent wall for slow tools "
        "should approach ~max(single), not ~N×single. Cheap hot searches may "
        "look similar under both modes (work too small / pool contention)."
    )


if __name__ == "__main__":
    main()
