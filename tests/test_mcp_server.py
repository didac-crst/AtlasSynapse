"""MCP transport server construction and protocol smoke tests."""

from __future__ import annotations

import io
import json

from semantic_memory.config import Settings
from semantic_memory.mcp import MCPServerInfo, build_mcp_server
from semantic_memory.mcp.server import build_mcp_tools
from semantic_memory.mcp.stdio import StdioMCPServer, ToolSpec


def _line(message: dict[str, object]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"


def test_build_mcp_server_registers_contract_tools() -> None:
    tools = {tool.name for tool in build_mcp_tools()}
    info = MCPServerInfo.from_settings(Settings(mcp_transport="stdio"))
    assert set(info.tools) == tools
    assert "create_entity" in tools
    assert "apply_ontology_proposal" in tools
    assert "apply_proposal" not in tools
    assert info.transport == "stdio"
    server = build_mcp_server(Settings(mcp_transport="stdio"))
    assert isinstance(server, StdioMCPServer)


def test_stdio_initialize_and_tools_list() -> None:
    tools = build_mcp_tools()
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
