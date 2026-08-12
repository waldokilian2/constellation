"""End-to-end tests for the MCP server (engine/mcp_server.py).

Run with: python tests/run_tests.py test_mcp_server

These exercise the server through the official MCP Python SDK client — the
same path real coding agents (Claude Code, Cursor) use — in two modes:

  * **In-memory** — ``Client(build_server(graph))``. Fast and deterministic;
    verifies tool/schema fidelity to TOOL_DEFINITIONS (the single source of
    truth), read-only annotations, dispatch via execute_tool, error semantics,
    and the graph resource.
  * **Subprocess over stdio** — spawns ``python -m engine.mcp_server`` and
    speaks the real JSON-RPC wire protocol. Verifies main(), graph load from
    disk, the stdio transport, capability advertisement, and that the
    negotiated protocol version is current (not the stale 2024-11-05).

Requires the ``mcp`` SDK (a runtime dep since the server was moved onto it)
and ``output/graph.json`` (gitignored — produced by start.sh / the engine).
Tests skip gracefully when either is absent rather than failing the suite.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GRAPH_FILE = REPO / "output" / "graph.json"

# All MCP-dependent imports are guarded so the module loads (and the runner
# can discover + skip these tests) even where the SDK isn't installed.
try:
    from mcp import Client, ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from engine.mcp_server import (
        GRAPH_RESOURCE_URI,
        SERVER_NAME,
        build_server,
    )
    from engine.graph_tools import TOOL_DEFINITIONS
    _READY = True
except ImportError:
    _READY = False


def _load_graph() -> dict:
    return json.loads(GRAPH_FILE.read_text())


# ── In-memory tests (handler logic + schema fidelity) ──────────────

def test_tools_advertise_single_source_of_truth_schema():
    """Each MCP tool's input_schema must equal TOOL_DEFINITIONS verbatim."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    tools = asyncio.run(_list_tools_inmemory())
    by_name = {t.name: t for t in tools}
    expected = {t["name"]: t for t in TOOL_DEFINITIONS}
    assert set(by_name) == set(expected), (set(by_name) ^ set(expected))
    for name, td in expected.items():
        assert by_name[name].description == td["description"], name
        # The schema the REST API / web AI use IS the schema MCP clients see.
        assert by_name[name].input_schema == td["parameters"], name


def test_tools_are_tagged_read_only():
    """Every Constellation tool is a pure graph read — clients may skip prompts."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    tools = asyncio.run(_list_tools_inmemory())
    assert tools, "no tools advertised"
    for t in tools:
        ann = t.annotations
        assert ann is not None and ann.read_only_hint is True, t.name


def test_call_tool_dispatches_through_execute_tool():
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    if not GRAPH_FILE.exists():
        print("  SKIP: output/graph.json missing")
        return
    graph = _load_graph()

    async def run():
        async with Client(build_server(graph)) as c:
            overview = await c.call_tool("get_architecture_overview", {})
            assert not overview.is_error
            data = json.loads(overview.content[0].text)
            assert data["total_repos"] == len(graph.get("repos", []))

            search = await c.call_tool("search_code", {"query": "order"})
            assert not search.is_error
            assert "entry_points" in json.loads(search.content[0].text)

    asyncio.run(run())


def test_unknown_tool_returns_is_error_not_protocol_error():
    """An unknown tool is a model-visible error result, not a dropped connection."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    if not GRAPH_FILE.exists():
        print("  SKIP: output/graph.json missing")
        return
    graph = _load_graph()

    async def run():
        async with Client(build_server(graph)) as c:
            r = await c.call_tool("does_not_exist", {})
            assert r.is_error is True
            assert "Unknown tool" in r.content[0].text

    asyncio.run(run())


def test_missing_graph_yields_helpful_error_result():
    """No graph.json → tool calls return is_error, not a crash or protocol error."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return

    async def run():
        async with Client(build_server({})) as c:
            r = await c.call_tool("search_code", {"query": "x"})
            assert r.is_error is True
            assert "graph.json" in r.content[0].text

    asyncio.run(run())


def test_graph_resource_roundtrip():
    """The constellation://graph resource serves the full in-memory graph."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    if not GRAPH_FILE.exists():
        print("  SKIP: output/graph.json missing")
        return
    graph = _load_graph()

    async def run():
        async with Client(build_server(graph)) as c:
            lr = await c.list_resources()
            assert any(r.uri == GRAPH_RESOURCE_URI for r in lr.resources)
            rr = await c.read_resource(GRAPH_RESOURCE_URI)
            served = json.loads(rr.contents[0].text)
            assert served == graph

    asyncio.run(run())


# ── Subprocess test (real stdio wire + main()) ─────────────────────

def test_subprocess_stdio_negotiates_current_protocol():
    """main() serves over stdio; protocol is current and capabilities are honest."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    if not GRAPH_FILE.exists():
        print("  SKIP: output/graph.json missing")
        return
    env = dict(os.environ)
    env["CONSTELLATION_GRAPH"] = str(GRAPH_FILE)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "engine.mcp_server"],
        env=env,
        cwd=str(REPO),
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                # Modernized away from the stale 2024-11-05 (ISO-date compare).
                assert str(init.protocol_version) > "2024-11-05", init.protocol_version
                assert init.server_info.name == SERVER_NAME

                caps = init.capabilities
                # The old server handled resources/* but never declared them.
                assert caps.tools is not None
                assert caps.resources is not None

                tools = await session.list_tools()
                assert len(tools.tools) == len(TOOL_DEFINITIONS)

                # A real query must succeed over the wire.
                r = await session.call_tool("list_channels", {})
                assert not r.is_error
                assert "channels" in json.loads(r.content[0].text)

    asyncio.run(run())


# ── in-memory helpers ──────────────────────────────────────────────

async def _list_tools_inmemory():
    if not GRAPH_FILE.exists():
        # Schema/annotation tests don't depend on graph contents; an empty
        # graph still advertises every tool.
        graph = {}
    else:
        graph = _load_graph()
    async with Client(build_server(graph)) as c:
        return (await c.list_tools()).tools
