"""MCP tool-surface filtering: agent catalog vs full registry."""

from __future__ import annotations

import io
import json

from semantic_memory.config import Settings
from semantic_memory.mcp.server import build_all_mcp_tools, build_mcp_tools
from semantic_memory.mcp.stdio import StdioMCPServer
from semantic_memory.mcp.surfaces import AGENT_TOOL_NAMES, McpToolSurface, surface_visible


def _line(message: dict[str, object]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"


def test_agent_surface_is_small_and_intent_shaped() -> None:
    tools = build_mcp_tools(settings=Settings(mcp_tool_surface="agent"))
    names = {tool.name for tool in tools}
    assert names == set(AGENT_TOOL_NAMES)
    assert len(names) == 13
    assert "search_memory" in names
    assert "get_ontology_proposal" in names
    assert "get_proposal" not in names  # advanced alias only — no agent duplicate
    assert "get_relevant_context" in names
    assert "assert_statement" in names
    assert "correct_statement" in names
    assert "retract_statement" in names
    assert "repair_supersession_integrity" in names
    assert "supersede_statement" not in names
    assert "get_entity_neighborhood" not in names
    assert "search_entities" not in names
    assert "create_entity" not in names
    assert "get_runtime_config" not in names


def test_advanced_includes_agent_plus_investigation() -> None:
    agent = {t.name for t in build_mcp_tools(settings=Settings(mcp_tool_surface="agent"))}
    advanced = {t.name for t in build_mcp_tools(settings=Settings(mcp_tool_surface="advanced"))}
    assert agent <= advanced
    assert "supersede_statement" in advanced
    assert "get_entity_neighborhood" in advanced
    assert "search_semantic_memory" in advanced
    assert "get_runtime_config" in advanced


def test_all_surface_equals_full_registry() -> None:
    all_names = {t.name for t in build_mcp_tools(settings=Settings(mcp_tool_surface="all"))}
    registry = {t.name for t in build_all_mcp_tools()}
    assert all_names == registry
    # Before aliases the catalog was ~40; full registry still large.
    assert len(registry) >= 40


def test_surface_visibility_is_not_authorization() -> None:
    """Hiding a tool does not imply it lacks capabilities — only catalog UX."""
    assert surface_visible("agent", McpToolSurface.AGENT)
    assert not surface_visible("agent", McpToolSurface.ADVANCED)
    assert surface_visible("advanced", McpToolSurface.AGENT)
    assert surface_visible("advanced", McpToolSurface.ADVANCED)
    assert not surface_visible("advanced", McpToolSurface.ADMIN)
    assert surface_visible("all", McpToolSurface.ADMIN)


def test_agent_tools_list_excludes_advanced_over_stdio() -> None:
    tools = build_mcp_tools(settings=Settings(mcp_tool_surface="agent"))
    stdin = io.BytesIO(
        _line({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + _line({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        + _line(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "supersede_statement",
                    "arguments": {"payload": {}},
                },
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
    lines = [json.loads(line) for line in stdout.getvalue().decode().splitlines() if line]
    listed = {tool["name"] for tool in lines[1]["result"]["tools"]}
    assert "search_memory" in listed
    assert "supersede_statement" not in listed
    # Unknown to this surface → method/tool error path
    call = lines[2]
    assert call["id"] == 3
    assert "error" in call or call.get("result", {}).get("isError")


def test_agent_scenario_coverage_catalog() -> None:
    """Main ChatGPT scenarios must be expressible with agent tools only."""
    names = {t.name for t in build_mcp_tools(settings=Settings(mcp_tool_surface="agent"))}
    scenarios = {
        "Who is Didac?": {"search_memory", "get_relevant_context"},
        "What about Airbus role?": {"search_memory", "get_relevant_context", "get_timeline"},
        "Correct start date": {"correct_statement"},
        "Store new fact": {"assert_statement"},
        "Resolve ambiguous Didac": {"answer_identity_clarification", "assert_statement"},
        "Propose ontology concept": {"propose_class", "propose_predicate"},
        "Apply approved proposal": {"get_ontology_proposal", "apply_ontology_proposal"},
        "Repair bad supersession edge": {"repair_supersession_integrity"},
    }
    for scenario, required_any in scenarios.items():
        assert names & required_any, f"{scenario} missing tools from {required_any}"
