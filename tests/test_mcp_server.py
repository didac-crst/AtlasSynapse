"""MCP transport server construction and protocol smoke tests."""

from __future__ import annotations

import io
import json

from semantic_memory.config import Settings
from semantic_memory.mcp import MCPServerInfo, build_mcp_server
from semantic_memory.mcp.server import build_mcp_tools
from semantic_memory.mcp.stdio import StdioMCPServer


def _frame(message: dict[str, object]) -> str:
    body = json.dumps(message, separators=(",", ":"))
    return f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"


def test_build_mcp_server_registers_contract_tools() -> None:
    tools = {tool.name for tool in build_mcp_tools()}
    info = MCPServerInfo.from_settings(Settings(mcp_transport="stdio"))
    assert set(info.tools) == tools
    assert "create_entity" in tools
    assert "apply_proposal" not in tools
    assert info.transport == "stdio"
    server = build_mcp_server(Settings(mcp_transport="stdio"))
    assert isinstance(server, StdioMCPServer)


def test_stdio_initialize_and_tools_list() -> None:
    tools = build_mcp_tools()
    stdin = io.StringIO(
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    )
    stdout = io.StringIO()
    server = StdioMCPServer(
        name="AtlasSynapseTest",
        instructions="test",
        tools=tools,
        stdin=stdin,
        stdout=stdout,
    )
    server.run()
    raw = stdout.getvalue()
    assert "AtlasSynapseTest" in raw
    assert "create_entity" in raw
    assert "propose_class" in raw
