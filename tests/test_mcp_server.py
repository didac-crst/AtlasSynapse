"""MCP transport server construction and protocol smoke tests."""

from __future__ import annotations

import io
import json
import threading
import time

from semantic_memory.config import Settings
from semantic_memory.mcp import MCPServerInfo, build_mcp_server
from semantic_memory.mcp.server import build_mcp_tools
from semantic_memory.mcp.stdio import StdioMCPServer, ToolSpec


def _line(message: dict[str, object]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"


def test_build_mcp_server_registers_contract_tools() -> None:
    settings = Settings(mcp_transport="stdio", mcp_tool_surface="all")
    tools = {tool.name for tool in build_mcp_tools(settings=settings)}
    info = MCPServerInfo.from_settings(settings)
    assert set(info.tools) == tools
    assert "create_entity" in tools
    assert "apply_ontology_proposal" in tools
    assert "search_memory" in tools
    assert "apply_proposal" not in tools
    assert info.transport == "stdio"
    server = build_mcp_server(settings)
    assert isinstance(server, StdioMCPServer)


def test_stdio_initialize_and_tools_list() -> None:
    tools = build_mcp_tools(settings=Settings(mcp_tool_surface="all"))
    stdin = io.BytesIO(
        _line({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + _line({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    )
    stdout = io.BytesIO()
    server = StdioMCPServer(
        name="AtlasSynapseTest",
        instructions="test",
        tools=tools,
        stdin=stdin,
        stdout=stdout,
    )
    server.run()
    raw = stdout.getvalue().decode("utf-8")
    assert "Content-Length" not in raw
    assert raw.count("\n") >= 2
    assert "AtlasSynapseTest" in raw
    assert "create_entity" in raw
    assert "propose_class" in raw


def test_stdio_newline_delimited_with_non_ascii() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"note": "café"}}
    stdin = io.BytesIO(_line(payload))
    stdout = io.BytesIO()
    server = StdioMCPServer(
        name="AtlasSynapseTest",
        instructions="test",
        tools=[],
        stdin=stdin,
        stdout=stdout,
    )
    server.run()
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0].decode("utf-8")) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }


def test_invalid_tool_arguments_are_sanitized() -> None:
    def _needs_id(*, entity_id: str) -> dict[str, object]:
        return {"entity_id": entity_id}

    tools = [
        ToolSpec(
            name="get_entity",
            description="Fetch an entity by id.",
            handler=_needs_id,
            input_schema={
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "required": ["entity_id"],
            },
        )
    ]
    stdin = io.BytesIO(
        _line(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_entity", "arguments": {}},
            }
        )
    )
    stdout = io.BytesIO()
    StdioMCPServer(
        name="AtlasSynapseTest",
        instructions="test",
        tools=tools,
        stdin=stdin,
        stdout=stdout,
    ).run()
    text = stdout.getvalue().decode("utf-8")
    assert "Invalid tool arguments" in text
    assert "required positional" not in text
    assert "entity_id" not in text
    assert "required" not in text.lower()


def test_concurrent_tools_call_preserves_ids_and_overlaps() -> None:
    """N slow tools/call should overlap when max_inflight > 1."""
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _slow(*, payload: dict[str, object]) -> dict[str, object]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.15)
        with lock:
            active -= 1
        return {"echo": payload.get("n")}

    tools = [
        ToolSpec(
            name="slow_echo",
            description="Sleep then echo.",
            handler=_slow,
            input_schema={"type": "object", "properties": {"payload": {"type": "object"}}},
        )
    ]
    n = 5
    stdin = io.BytesIO(
        b"".join(
            _line(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": "slow_echo", "arguments": {"payload": {"n": i}}},
                }
            )
            for i in range(1, n + 1)
        )
    )
    stdout = io.BytesIO()
    started = time.perf_counter()
    StdioMCPServer(
        name="AtlasSynapseTest",
        instructions="test",
        tools=tools,
        stdin=stdin,
        stdout=stdout,
        max_inflight=5,
    ).run()
    wall = time.perf_counter() - started
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == n
    ids = {msg["id"] for msg in lines}
    assert ids == set(range(1, n + 1))
    for msg in lines:
        assert msg["jsonrpc"] == "2.0"
        assert "result" in msg
        assert msg["result"]["isError"] is False
    # Serial would be ~0.75s; concurrent should be well under 0.5s.
    assert wall < 0.55, wall
    assert max_active >= 2, max_active


def test_max_inflight_one_is_single_flight() -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _slow(*, payload: dict[str, object]) -> dict[str, object]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return {"ok": True}

    tools = [
        ToolSpec(
            name="slow_echo",
            description="Sleep.",
            handler=_slow,
            input_schema={"type": "object"},
        )
    ]
    stdin = io.BytesIO(
        b"".join(
            _line(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": "slow_echo", "arguments": {"payload": {}}},
                }
            )
            for i in range(1, 4)
        )
    )
    stdout = io.BytesIO()
    StdioMCPServer(
        name="AtlasSynapseTest",
        instructions="test",
        tools=tools,
        stdin=stdin,
        stdout=stdout,
        max_inflight=1,
    ).run()
    assert max_active == 1
    assert {json.loads(line)["id"] for line in stdout.getvalue().splitlines()} == {1, 2, 3}
