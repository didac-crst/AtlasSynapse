"""Minimal MCP JSON-RPC stdio transport (newline-delimited JSON).

Per MCP 2024-11-05 stdio transport, messages are delimited by newlines and must
not contain embedded newlines. Handlers remain plain callables that return
JSON-serializable dicts.

``tools/call`` may run concurrently (thread pool) while stdout writes stay
serialized under a lock. JSON-RPC response ``id`` correlates replies; response
order need not match request order.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, BinaryIO

ToolHandler = Callable[..., dict[str, Any]]
logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: ToolHandler
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
    )


class StdioMCPServer:
    """MCP server over newline-delimited JSON stdio."""

    def __init__(
        self,
        *,
        name: str,
        instructions: str,
        tools: list[ToolSpec],
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        max_inflight: int = 8,
    ) -> None:
        if max_inflight < 1:
            raise ValueError("max_inflight must be >= 1")
        self._name = name
        self._instructions = instructions
        self._tools = {tool.name: tool for tool in tools}
        self._stdin: BinaryIO = stdin or sys.stdin.buffer
        self._stdout: BinaryIO = stdout or sys.stdout.buffer
        self._max_inflight = max_inflight
        self._write_lock = threading.Lock()

    def run(self) -> None:
        """Read forever; dispatch ``tools/call`` concurrently up to ``max_inflight``.

        stdio/JSON-RPC do not require serial execution. This server keeps stdout
        writes locked and correlates responses via JSON-RPC ``id``.
        """
        pending: set[Future[None]] = set()
        with ThreadPoolExecutor(
            max_workers=self._max_inflight, thread_name_prefix="mcp-tool"
        ) as pool:
            while True:
                try:
                    message = self._read_message()
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError):
                    self._safe_write(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Parse error"},
                        }
                    )
                    continue
                if message is None:
                    break

                method = message.get("method")
                if method == "tools/call" and self._max_inflight > 1:
                    future = pool.submit(self._dispatch_and_write, message)
                    pending.add(future)

                    def _done(fut: Future[None], *, _pending: set[Future[None]] = pending) -> None:
                        _pending.discard(fut)
                        try:
                            fut.result()
                        except Exception:
                            logger.exception("MCP concurrent tools/call worker failed")

                    future.add_done_callback(_done)
                else:
                    # Serial path: initialize/list/ping, or max_inflight==1.
                    self._dispatch_and_write(message)

            for fut in list(pending):
                try:
                    fut.result()
                except Exception:
                    logger.exception("MCP worker failed during shutdown drain")

    def _dispatch_and_write(self, message: dict[str, Any]) -> None:
        response = self._dispatch(message)
        if response is not None:
            self._safe_write(response)

    def _safe_write(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            self._write_message(message)

    def _read_message(self) -> dict[str, Any] | None:
        while True:
            line = self._stdin.readline()
            if line == b"":
                return None
            stripped = line.strip()
            if not stripped:
                continue
            data = json.loads(stripped.decode("utf-8"))
            if not isinstance(data, dict):
                raise TypeError("MCP message must be a JSON object")
            return data

    def _write_message(self, message: dict[str, Any]) -> None:
        # Compact JSON with no literal newlines (NDJSON-safe). Caller holds lock.
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        self._stdout.write(body.encode("utf-8"))
        self._stdout.write(b"\n")
        self._stdout.flush()

    def _dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if method == "initialize":
            from semantic_memory.runtime_info import package_version, runtime_config

            cfg = runtime_config()
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": self._name,
                        "version": package_version(),
                    },
                    "instructions": (
                        f"{self._instructions} "
                        f"semantic_review_mode={cfg.get('semantic_review_mode')} "
                        f"version={cfg.get('version')} "
                        f"mcp_max_inflight={self._max_inflight}."
                    ),
                },
            }
        if method == "notifications/initialized" or method == "initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.input_schema,
                        }
                        for tool in self._tools.values()
                    ]
                },
            }
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool = self._tools.get(name)
            if tool is None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                }
            started = time.perf_counter()
            try:
                result = tool.handler(**arguments)
            except TypeError:
                logger.info(
                    "mcp_tool_timing",
                    extra={
                        "event": "mcp_tool_timing",
                        "tool": name,
                        "ok": False,
                        "error": "invalid_arguments",
                        "timings_ms": {
                            "total": round((time.perf_counter() - started) * 1000, 3)
                        },
                    },
                )
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Invalid tool arguments",
                            }
                        ],
                        "isError": True,
                    },
                }
            except Exception:
                logger.exception("MCP tool %s failed", name)
                logger.info(
                    "mcp_tool_timing",
                    extra={
                        "event": "mcp_tool_timing",
                        "tool": name,
                        "ok": False,
                        "error": "exception",
                        "timings_ms": {
                            "total": round((time.perf_counter() - started) * 1000, 3)
                        },
                    },
                )
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Tool execution failed",
                            }
                        ],
                        "isError": True,
                    },
                }
            serialize_started = time.perf_counter()
            text = json.dumps(result, ensure_ascii=False)
            serialize_ms = round((time.perf_counter() - serialize_started) * 1000, 3)
            total_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.info(
                "mcp_tool_timing",
                extra={
                    "event": "mcp_tool_timing",
                    "tool": name,
                    "ok": True,
                    "response_bytes": len(text.encode("utf-8")),
                    "timings_ms": {
                        "handler": round(total_ms - serialize_ms, 3),
                        "serialization": serialize_ms,
                        "total": total_ms,
                    },
                },
            )
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                        }
                    ],
                    "structuredContent": result,
                    "isError": bool(
                        isinstance(result, dict) and result.get("error_code") is not None
                    ),
                },
            }
        if msg_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
