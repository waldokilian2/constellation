#!/usr/bin/env python3
"""
Constellation MCP Server.

Exposes the Constellation graph tools as an MCP (Model Context Protocol)
server. This lets external coding agents (Claude Code, Cursor, etc.) query
the codebase graphs that Constellation extracted.

Built on the official MCP Python SDK (v2, low-level ``Server``) — see
https://py.sdk.modelcontextprotocol.io/. The protocol version is negotiated
automatically by the SDK; we no longer hand-roll JSON-RPC.

We use the SDK's **low-level** API on purpose: it accepts an explicit
``input_schema`` dict per tool, so the single source of truth
(``engine.graph_tools.TOOL_DEFINITIONS``) stays intact — the same schemas the
REST API and the web AI tool-loop use are advertised to MCP clients verbatim.
Tool calls dispatch through the shared ``execute_tool``.

**Multi-project.** The server is wired to the app's ``ProjectStore``: every
graph tool takes an optional ``project`` argument (a project id from
``list_projects``) and dispatches to that project's graph. Omit it to query
the default project (the most recently updated *ready* project). This matches
what the web UI shows — one MCP server, all projects.

Transports:
  * **stdio** — ``python -m engine.mcp_server`` (local / non-docker).
  * **Streamable HTTP** — ``server.py`` mounts the same server at ``/mcp``,
    so the docker container serves MCP with just a URL.

Usage:
    # Standalone — serves every project in the local ProjectStore over stdio
    python -m engine.mcp_server

    # Register with Claude Code (in .mcp.json or ~/.claude/mcp.json):
    {
      "mcpServers": {
        "constellation": {
          "command": "python",
          "args": ["-m", "engine.mcp_server"],
          "cwd": "/path/to/constellation"
        }
      }
    }

    # Streamable HTTP (docker / web-server hosting): server.py mounts the
    # same MCP server at /mcp, so the docker container serves MCP with just
    # a URL and no stdio subprocess:
    {
      "mcpServers": {
        "constellation": {
          "type": "http",
          "url": "http://localhost:8765/mcp"
        }
      }
    }

Project graphs are loaded lazily on first query and cached. Restart the
server (or the agent) to pick up new/changed projects.
"""
from __future__ import annotations
import asyncio
import copy
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
    Tool,
    ToolAnnotations,
)

from engine.graph_tools import TOOL_DEFINITIONS, execute_tool

SERVER_NAME = "constellation"
SERVER_VERSION = "0.1.0"
GRAPH_RESOURCE_URI = "constellation://graph"

# Every Constellation tool is a pure read over the in-memory graph (and
# ``get_source`` reads source files but never mutates). Tagging them
# read-only is accurate and lets clients (and users) skip confirmations.
_READ_ONLY = ToolAnnotations(read_only_hint=True)

# Description appended to the ``project`` selector argument added to every
# graph tool when the server is project-aware.
_PROJECT_ARG_DESC = (
    "Project id to query (see the list_projects tool). "
    "Omit to query the default project (most recently updated ready project)."
)
_LIST_PROJECTS = "list_projects"


# ── Graph loading (single-graph / legacy fallback) ─────────────────

def _find_graph_path() -> Path | None:
    """Find graph.json — check env var, then common locations."""
    # 1. Explicit env var
    env_path = os.environ.get("CONSTELLATION_GRAPH", "")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # 2. Relative to this file (engine/ → parent → output/graph.json)
    here = Path(__file__).parent.parent
    p = here / "output" / "graph.json"
    if p.exists():
        return p

    # 3. Current working directory
    p = Path.cwd() / "output" / "graph.json"
    if p.exists():
        return p

    # 4. Current working directory directly
    p = Path.cwd() / "graph.json"
    if p.exists():
        return p

    return None


def _load_graph() -> dict:
    """Load a single graph.json (legacy fallback), or {} if not found."""
    graph_path = _find_graph_path()
    if not graph_path:
        return {}
    try:
        with open(graph_path) as f:
            return json.load(f)
    except Exception as e:
        sys.stderr.write(f"Constellation MCP: Failed to load graph: {e}\n")
        return {}


# ── Project graph resolver (backed by a ProjectStore) ──────────────

class _ProjectGraphs:
    """Resolve a project selector to its graph, backed by a ProjectStore.

    Duck-typed: ``store`` only needs ``list_projects() -> list[meta]`` and
    ``load_graph(pid) -> dict`` (so tests can pass a lightweight fake).
    Only *ready* projects are queryable; the most recently updated ready
    project is the default.
    """

    def __init__(self, store):
        # store.list_projects() returns newest-first by updated_at.
        self.all_projects: list[dict] = list(store.list_projects())
        self.ready: list[dict] = [p for p in self.all_projects if p.get("status") == "ready"]
        self.default_project: str | None = self.ready[0]["id"] if self.ready else None
        self._store = store
        self._cache: dict[str, dict] = {}

    @property
    def ready_ids(self) -> list[str]:
        return [p["id"] for p in self.ready]

    def graph(self, project: str | None = None) -> dict:
        """Return the graph for ``project`` (or the default). KeyError if unknown."""
        pid = project or self.default_project
        if pid is None or not any(p["id"] == pid for p in self.ready):
            raise KeyError(pid)
        if pid not in self._cache:
            self._cache[pid] = self._store.load_graph(pid)
        return self._cache[pid]


def _projects_payload(projects: list[dict]) -> dict:
    """list_projects result: every project with id/name/status/repos/stats."""
    return {
        "projects": [
            {
                "id": p["id"],
                "name": p.get("name", p["id"]),
                "status": p.get("status", "ready"),
                "repos": [
                    (r["name"] if isinstance(r, dict) else r)
                    for r in p.get("repos", [])
                ],
                "stats": p.get("stats", {}),
            }
            for p in projects
        ]
    }


# ── Tool + resource metadata ───────────────────────────────────────

def _build_tools() -> list[Tool]:
    """Single-graph tool list: TOOL_DEFINITIONS advertised verbatim."""
    return [
        Tool(
            name=t["name"],
            description=t["description"],
            input_schema=t["parameters"],
            annotations=_READ_ONLY,
        )
        for t in TOOL_DEFINITIONS
    ]


def _build_project_tools(project_ids: list[str]) -> list[Tool]:
    """Project-aware tool list: every tool gains an optional ``project``
    argument (deep-copied schema — never mutate TOOL_DEFINITIONS), plus a
    ``list_projects`` discovery tool.
    """
    tools: list[Tool] = []
    for t in TOOL_DEFINITIONS:
        schema = copy.deepcopy(t["parameters"])
        props = schema.setdefault("properties", {})
        props["project"] = {"type": "string", "enum": list(project_ids), "description": _PROJECT_ARG_DESC}
        tools.append(Tool(
            name=t["name"],
            description=t["description"],
            input_schema=schema,
            annotations=_READ_ONLY,
        ))
    tools.append(Tool(
        name=_LIST_PROJECTS,
        description=(
            "List every Constellation project this server can see, with id, "
            "name, status, repos, and stats. Pass a project id to any other "
            "tool's `project` argument to query that project. Only projects "
            "with status 'ready' are queryable."
        ),
        input_schema={"type": "object", "properties": {}},
        annotations=_READ_ONLY,
    ))
    return tools


def _graph_resource() -> Resource:
    """Expose the default project's graph as a single MCP resource."""
    return Resource(
        uri=GRAPH_RESOURCE_URI,
        name="Constellation Graph",
        description="The default project's codebase graph (use list_projects + the project arg for others)",
        mimeType="application/json",
    )


def _text_result(result: dict, *, is_error: bool | None = None) -> CallToolResult:
    """Wrap a tool result dict as an MCP CallToolResult."""
    if is_error is None:
        is_error = bool(isinstance(result, dict) and result.get("error"))
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, indent=2, default=str))],
        is_error=is_error,
    )


# ── Server factory ─────────────────────────────────────────────────

def _make_server(
    resolve,                      # callable(project: str | None) -> dict
    projects: list[dict],         # for list_projects (may be [])
    project_ids: list[str],       # queryable ids (ready)
) -> Server:
    """Build the low-level MCP server around a graph resolver.

    ``resolve(project)`` returns the graph for a project (or raises KeyError
    for an unknown/unavailable project). When ``project_ids`` is non-empty
    the server is project-aware (tools carry a ``project`` arg + list_projects
    is advertised); otherwise it behaves as a single-graph server.
    """
    project_aware = bool(project_ids)
    tools = _build_project_tools(project_ids) if project_aware else _build_tools()

    async def on_list_tools(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def on_call_tool(
        ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        name = params.name
        args = dict(params.arguments or {})

        if name == _LIST_PROJECTS:
            return _text_result(_projects_payload(projects))

        # Pick the graph: project-aware servers honor an optional `project`
        # arg (stripped before dispatch so execute_tool never sees it).
        requested_project = args.pop("project", None) if project_aware else None
        try:
            graph = resolve(requested_project)
        except KeyError:
            return _text_result({
                "error": (
                    f"No graph for project {requested_project!r}. "
                    "Call the list_projects tool for available ids."
                ),
            }, is_error=True)

        if not graph:
            if project_aware:
                msg = ("No graph loaded for that project. Re-run the Constellation "
                       "engine for it, or pick another id from list_projects.")
            else:
                msg = ("No graph.json loaded. Run the Constellation engine first "
                       "or set the CONSTELLATION_GRAPH environment variable.")
            return _text_result({"error": msg}, is_error=True)

        # execute_tool validates args against the schema and catches internal
        # errors, returning {"error": ...}. Wrap defensively anyway so an
        # unexpected failure stays a model-visible tool result, not a hard
        # protocol error.
        try:
            result = execute_tool(graph, name, args)
        except Exception as e:  # pragma: no cover - execute_tool shouldn't raise
            result = {"error": f"Tool '{name}' failed: {e}"}
        return _text_result(result)

    async def on_list_resources(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListResourcesResult:
        return ListResourcesResult(resources=[_graph_resource()])

    async def on_read_resource(
        ctx: ServerRequestContext, params: ReadResourceRequestParams
    ) -> ReadResourceResult:
        if params.uri == GRAPH_RESOURCE_URI:
            try:
                graph = resolve(None)   # default project's graph
            except KeyError:
                graph = {}
            return ReadResourceResult(contents=[TextResourceContents(
                uri=GRAPH_RESOURCE_URI,
                mimeType="application/json",
                text=json.dumps(graph, indent=2, default=str),
            )])
        # Unknown resource → protocol error (resources have no is_error half-result).
        raise ValueError(f"Unknown resource: {params.uri}")

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
    )


def build_server(graph: dict) -> Server:
    """Single-graph server: every tool runs against this one ``graph``."""
    def resolve(_project: str | None = None) -> dict:
        return graph
    return _make_server(resolve, [], [])


def build_project_server(store) -> Server:
    """Project-aware server backed by a ProjectStore (list_projects + load_graph)."""
    pg = _ProjectGraphs(store)
    return _make_server(pg.graph, pg.all_projects, pg.ready_ids)


def mount_streamable_http(app, graph: dict | None = None, store=None) -> bool:
    """Expose the graph tools over Streamable HTTP at ``app``/mcp.

    Mounts an MCP Streamable HTTP endpoint at ``/mcp`` on an existing ASGI
    app — the FastAPI web app in ``server.py``. This is how the docker
    container (which runs that web app) serves MCP to clients with just a
    URL and no stdio subprocess::

        "constellation": { "type": "http", "url": "http://localhost:8765/mcp" }

    If ``store`` is given the server is project-aware (one server, all
    projects). Otherwise it falls back to a single ``graph`` (or the legacy
    graph.json). The app's existing lifespan (if any) is preserved and
    wrapped so the MCP session manager's lifecycle runs for the app's
    lifetime (the SDK's Streamable HTTP transport requires a running task
    group).

    Returns True when mounted; False when the ``mcp`` SDK is unavailable —
    the web app keeps running either way, and local stdio MCP is unaffected.
    """
    try:
        from mcp.server.streamable_http_manager import (
            StreamableHTTPSessionManager,
            StreamableHTTPASGIApp,
        )
    except Exception as e:
        sys.stderr.write(f"Constellation MCP: /mcp not mounted (mcp SDK unavailable: {e})\n")
        return False

    if store is not None:
        server = build_project_server(store)
        pg = _ProjectGraphs(store)
        label = f"{len(pg.ready)} ready project(s) (default: {pg.default_project})"
    else:
        if graph is None:
            graph = _load_graph()
        server = build_server(graph)
        label = f"single graph: {len(graph.get('repos', []))} repos, {len(graph.get('entry_points', []))} entry points"

    manager = StreamableHTTPSessionManager(server)

    # Wrap the app's existing lifespan (Starlette runs this at startup) so
    # the session manager's task group lives for the app's lifetime. Starlette
    # does `async with lifespan_context(app)`, so the lifespan must be a
    # proper async context manager, not a bare async generator.
    prev = getattr(app.router, "lifespan_context", None)
    if prev is None:
        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield
        app.router.lifespan_context = lifespan
    else:
        @asynccontextmanager
        async def lifespan(app):
            async with prev(app):
                async with manager.run():
                    yield
        app.router.lifespan_context = lifespan

    app.mount("/mcp", StreamableHTTPASGIApp(manager))
    sys.stderr.write(f"Constellation MCP: Streamable HTTP mounted at /mcp ({label})\n")
    return True


async def _serve(server: Server) -> None:
    """Run ``server`` over the stdio transport until the client disconnects."""
    # stdout is the protocol wire — the SDK redirects flushed stdout to stderr
    # during serving, so any stray prints can't corrupt the stream. Diagnostics
    # here go to stderr explicitly.
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Serve MCP over stdio — project-aware when a ProjectStore has projects."""
    repo_root = Path(__file__).resolve().parent.parent

    store = None
    pg = None
    try:
        from engine.project_store import ProjectStore
        store = ProjectStore(repo_root)
        pg = _ProjectGraphs(store)
    except Exception as e:  # pragma: no cover - store is stdlib-free, unlikely
        sys.stderr.write(f"Constellation MCP: project store unavailable ({e}); falling back to single graph\n")

    if pg and pg.ready:
        sys.stderr.write(
            f"Constellation MCP: serving {len(pg.ready)} project(s) "
            f"(default: {pg.default_project}) over stdio\n"
        )
        server = build_project_server(store)
    else:
        graph = _load_graph()
        sys.stderr.write(
            f"Constellation MCP: no ready projects in store; serving single graph "
            f"({len(graph.get('repos', []))} repos, "
            f"{len(graph.get('entry_points', []))} entry points)\n"
        )
        server = build_server(graph)

    asyncio.run(_serve(server))


if __name__ == "__main__":
    main()
