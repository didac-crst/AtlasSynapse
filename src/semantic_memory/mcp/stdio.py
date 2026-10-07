"""Minimal MCP JSON-RPC stdio transport (newline-delimited JSON).

Per MCP 2024-11-05 stdio transport, messages are delimited by newlines and must
not contain embedded newlines. Handlers remain plain callables that return
JSON-serializable dicts.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
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
    """Very small MCP server over newline-delimited JSON stdio."""

    def __init__(
        self,
        *,
        name: str,
        instructions: str,
        tools: list[ToolSpec],
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
    ) -> None:
        self._name = name
        self._instructions = instructions
        self._tools = {tool.name: tool for tool in tools}
        self._stdin: BinaryIO = stdin or sys.stdin.buffer
        self._stdout: BinaryIO = stdout or sys.stdout.buffer

    def run(self) -> None:
        """Serve requests one-at-a-time on this thread.

        This single-flight loop is an implementation choice: JSON-RPC request
        ids could support concurrent dispatch with synchronized stdout writes.
        stdio transport itself does not require serial execution.
        """
        while True:
            try:
                message = self._read_message()
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError):
                self._write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                )
                continue
            if message is None:
                return
            response = self._dispatch(message)
            if response is not None:
                self._write_message(response)

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
        # Compact JSON with no literal newlines (NDJSON-safe).
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
                        f"version={cfg.get('version')}."
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
            import time

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
