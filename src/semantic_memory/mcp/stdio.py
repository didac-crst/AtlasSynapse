"""Minimal MCP JSON-RPC stdio transport (Content-Length framing).

Keeps business logic out of the transport: handlers are plain callables that
return JSON-serializable dicts. Compatible with MCP tool list/call flows used
by agent hosts.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TextIO

ToolHandler = Callable[..., dict[str, Any]]


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
    """Very small MCP server over stdin/stdout."""

    def __init__(
        self,
        *,
        name: str,
        instructions: str,
        tools: list[ToolSpec],
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self._name = name
        self._instructions = instructions
        self._tools = {tool.name: tool for tool in tools}
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout

    def run(self) -> None:
        while True:
            message = self._read_message()
            if message is None:
                return
            response = self._dispatch(message)
            if response is not None:
                self._write_message(response)

    def _read_message(self) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = self._stdin.readline()
            if line == "":
                return None
            if line in {"\n", "\r\n"}:
                break
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        length_raw = headers.get("content-length")
        if not length_raw:
            return None
        payload = self._stdin.read(int(length_raw))
        if not payload:
            return None
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise TypeError("MCP message must be a JSON object")
        return data

    def _write_message(self, message: dict[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        encoded = body.encode("utf-8")
        self._stdout.write(f"Content-Length: {len(encoded)}\r\n\r\n")
        self._stdout.write(encoded.decode("utf-8"))
        self._stdout.flush()

    def _dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self._name, "version": "0.1.0"},
                    "instructions": self._instructions,
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
            try:
                result = tool.handler(**arguments)
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False),
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
