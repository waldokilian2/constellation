"""End-to-end tests for the MCP server (engine/mcp_server.py).

Run with: python tests/run_tests.py test_mcp_server

These exercise the server through the official MCP Python SDK client — the
same path real coding agents (Claude Code, Cursor) use — across three modes:

  * **In-memory** — ``Client(build_server(graph))``. Fast and deterministic;
    verifies tool/schema fidelity to TOOL_DEFINITIONS (the single source of
    truth), read-only annotations, dispatch via execute_tool, error semantics,
    and the graph resource.
  * **Subprocess over stdio** — spawns ``python -m engine.mcp_server`` and
    speaks the real JSON-RPC wire protocol. Verifies main(), graph load from
    disk, the stdio transport, capability advertisement, and that the
    negotiated protocol version is current (not the stale 2024-11-05).
  * **Streamable HTTP** — mounts ``mount_streamable_http`` (the docker path:
    ``server.py`` serves the same MCP server at ``/mcp``) on a live app and
    connects over HTTP, verifying the container / web-server transport.

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
        build_project_server,
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
                tool_names = {t.name for t in tools.tools}
                # Every graph tool from the single source of truth is advertised.
                assert {t["name"] for t in TOOL_DEFINITIONS}.issubset(tool_names)
                # The stdio server is project-aware when the local store has a
                # ready project, so it also advertises list_projects.
                assert "list_projects" in tool_names

                # A real query must succeed over the wire.
                r = await session.call_tool("list_channels", {})
                assert not r.is_error
                assert "channels" in json.loads(r.content[0].text)

    asyncio.run(run())


def test_streamable_http_transport():
    """The same MCP server is served over Streamable HTTP (the docker path).

    server.py mounts build_server() at /mcp via mount_streamable_http; this
    mounts the same helper on a live app, serves it, and connects over HTTP —
    exactly how the docker container exposes MCP to clients.
    """
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    if not GRAPH_FILE.exists():
        print("  SKIP: output/graph.json missing")
        return

    import socket
    import threading

    import uvicorn
    from fastapi import FastAPI
    from engine.mcp_server import mount_streamable_http

    app = FastAPI()
    assert mount_streamable_http(app) is True

    # Bind an ephemeral port, then hand it to uvicorn.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    threading.Thread(target=srv.run, daemon=True).start()

    async def run():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        url = f"http://127.0.0.1:{port}/mcp"
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert str(init.protocol_version) > "2024-11-05", init.protocol_version
                assert init.capabilities.tools is not None
                assert init.capabilities.resources is not None

                tools = await session.list_tools()
                assert len(tools.tools) == len(TOOL_DEFINITIONS)

                res = await session.call_tool("get_architecture_overview", {})
                assert not res.is_error
                assert "total_repos" in res.content[0].text

    try:
        asyncio.run(run())
    finally:
        srv.should_exit = True


# ── Multi-project tests (list_projects + project-scoped dispatch) ───

class _FakeStore:
    """Minimal stand-in for ProjectStore: list_projects() + load_graph(pid)."""

    def __init__(self, projects: list[dict], graphs: dict):
        self._projects = projects
        self._graphs = graphs

    def list_projects(self) -> list[dict]:
        return list(self._projects)

    def load_graph(self, pid: str) -> dict:
        return self._graphs[pid]


def _fake_project_store() -> "_FakeStore":
    # newest first (ProjectStore.list_projects sorts by updated_at desc);
    # gamma is errored — present in the list but NOT queryable.
    projects = [
        {"id": "alpha", "name": "Alpha", "status": "ready",
         "repos": [{"name": "alpha"}], "stats": {"repos": 1, "entry_points": 1},
         "updated_at": "2026-08-12T00:00:00+00:00"},
        {"id": "beta", "name": "Beta", "status": "ready",
         "repos": [{"name": "beta"}], "stats": {"repos": 1, "entry_points": 1},
         "updated_at": "2026-08-11T00:00:00+00:00"},
        {"id": "gamma", "name": "Gamma", "status": "error",
         "repos": [{"name": "gamma"}], "stats": {},
         "updated_at": "2026-08-10T00:00:00+00:00"},
    ]
    graphs = {
        "alpha": {"repos": ["alpha"], "entry_points": [
            {"id": "alpha:EP.run", "repo": "alpha", "type": "rest-endpoint",
             "channel": "/run", "method": "run", "metrics": {"total_nodes": 1}}]},
        "beta": {"repos": ["beta"], "entry_points": [
            {"id": "beta:EP.run", "repo": "beta", "type": "rest-endpoint",
             "channel": "/run", "method": "run", "metrics": {"total_nodes": 1}}]},
    }
    return _FakeStore(projects, graphs)


def test_project_server_lists_all_projects():
    """list_projects shows every project (incl. errored); only ready ones are queryable."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    store = _fake_project_store()

    async def run():
        async with Client(build_project_server(store)) as c:
            lt = await c.list_tools()
            names = {t.name for t in lt.tools}
            assert "list_projects" in names
            # every graph tool carries a `project` enum of READY ids only
            sc = next(t for t in lt.tools if t.name == "search_code")
            assert set(sc.input_schema["properties"]["project"]["enum"]) == {"alpha", "beta"}

            r = await c.call_tool("list_projects", {})
            d = json.loads(r.content[0].text)
            assert [p["id"] for p in d["projects"]] == ["alpha", "beta", "gamma"]
            assert [p["id"] for p in d["projects"] if p["status"] == "ready"] == ["alpha", "beta"]

    asyncio.run(run())


def test_project_arg_dispatches_to_that_project():
    """Passing `project` selects that project's graph; omitting uses the default."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    store = _fake_project_store()

    async def run():
        async with Client(build_project_server(store)) as c:
            a = json.loads((await c.call_tool("get_architecture_overview", {"project": "alpha"})).content[0].text)
            b = json.loads((await c.call_tool("get_architecture_overview", {"project": "beta"})).content[0].text)
            assert a["repos"] == ["alpha"] and b["repos"] == ["beta"]
            # default = alpha (most recently updated ready project)
            d = json.loads((await c.call_tool("get_architecture_overview", {})).content[0].text)
            assert d["repos"] == ["alpha"]

    asyncio.run(run())


def test_unknown_project_returns_is_error():
    """An unknown project id is a model-visible error, not a protocol error."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    store = _fake_project_store()

    async def run():
        async with Client(build_project_server(store)) as c:
            r = await c.call_tool("get_architecture_overview", {"project": "nope"})
            assert r.is_error is True
            assert "list_projects" in r.content[0].text

    asyncio.run(run())


def test_project_graph_resource_serves_default_project():
    """constellation://graph serves the default project's graph."""
    if not _READY:
        print("  SKIP: mcp SDK not installed")
        return
    store = _fake_project_store()

    async def run():
        async with Client(build_project_server(store)) as c:
            rr = await c.read_resource(GRAPH_RESOURCE_URI)
            g = json.loads(rr.contents[0].text)
            assert g["repos"] == ["alpha"]  # default project

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
