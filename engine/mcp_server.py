#!/usr/bin/env python3
"""
Constellation MCP Server.

Exposes the Constellation graph tools as an MCP (Model Context Protocol)
server over stdio. This lets external coding agents (Claude Code, Cursor,
etc.) query the codebase graph that Constellation extracted.

Built on the official MCP Python SDK (v2, low-level ``Server``) — see
https://py.sdk.modelcontextprotocol.io/. The protocol version is negotiated
automatically by the SDK (current spec: 2026-07-28); we no longer hand-roll
JSON-RPC.

We use the SDK's **low-level** API on purpose: it accepts an explicit
``input_schema`` dict per tool, so the single source of truth
(``engine.graph_tools.TOOL_DEFINITIONS``) stays intact — the same schemas the
REST API and the web AI tool-loop use are advertised to MCP clients verbatim.
Tool calls dispatch through the shared ``execute_tool``.

Usage:
    # Standalone — loads graph.json from the current directory
    python -m engine.mcp_server

    # With a specific graph file
    CONSTELLATION_GRAPH=/path/to/graph.json python -m engine.mcp_server

    # Register with Claude Code (in .mcp.json or ~/.claude/mcp.json):
    {
      "mcpServers": {
        "constellation": {
          "command": "python",
          "args": ["-m", "engine.mcp_server"],
          "cwd": "/path/to/constellation",
          "env": {
            "CONSTELLATION_GRAPH": "/path/to/constellation/output/graph.json"
          }
        }
      }
    }

The server reads graph.json once at startup and serves all queries from
memory. Restart the server (or the agent) to pick up graph changes.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
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


# ── Graph loading ──────────────────────────────────────────────────

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
    """Load the graph, with a clear error message if not found."""
    graph_path = _find_graph_path()
    if not graph_path:
        return {}
    try:
        with open(graph_path) as f:
            return json.load(f)
    except Exception as e:
        sys.stderr.write(f"Constellation MCP: Failed to load graph: {e}\n")
        return {}


# ── Tool + resource metadata (from the single source of truth) ─────

def _build_tools() -> list[Tool]:
    """Advertise every TOOL_DEFINITIONS entry as an MCP tool.

    ``parameters`` IS the MCP ``inputSchema`` (JSON Schema), passed straight
    through so the schema the REST API and web AI use is exactly what MCP
    clients see.
    """
    return [
        Tool(
            name=t["name"],
            description=t["description"],
            input_schema=t["parameters"],
            annotations=_READ_ONLY,
        )
        for t in TOOL_DEFINITIONS
    ]


def _graph_resource() -> Resource:
    """Expose the full in-memory graph as a single MCP resource."""
    return Resource(
        uri=GRAPH_RESOURCE_URI,
        name="Constellation Graph",
        description="The full codebase graph extracted by Constellation",
        mimeType="application/json",
    )


# ── Server factory ─────────────────────────────────────────────────

def build_server(graph: dict) -> Server:
    """Build the low-level MCP server, closing its handlers over ``graph``."""
    tools = _build_tools()

    async def on_list_tools(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def on_call_tool(
        ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        name = params.name
        args = params.arguments or {}

        if not graph:
            # Surface as a model-visible tool error (not a protocol error)
            # so the agent learns it needs to (re)generate the graph.
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text="Error: No graph.json loaded. Run the Constellation engine first "
                         "or set the CONSTELLATION_GRAPH environment variable.",
                )],
                is_error=True,
            )

        # execute_tool validates args against the schema and catches internal
        # errors, returning {"error": ...}. Wrap defensively anyway so an
        # unexpected failure stays a model-visible tool result, not a hard
        # protocol error.
        try:
            result = execute_tool(graph, name, args)
        except Exception as e:  # pragma: no cover - execute_tool shouldn't raise
            result = {"error": f"Tool '{name}' failed: {e}"}

        text = json.dumps(result, indent=2, default=str)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            is_error=bool(isinstance(result, dict) and result.get("error")),
        )

    async def on_list_resources(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListResourcesResult:
        return ListResourcesResult(resources=[_graph_resource()])

    async def on_read_resource(
        ctx: ServerRequestContext, params: ReadResourceRequestParams
    ) -> ReadResourceResult:
        if params.uri == GRAPH_RESOURCE_URI:
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


async def _serve(server: Server) -> None:
    """Run ``server`` over the stdio transport until the client disconnects."""
    # stdout is the protocol wire — the SDK redirects flushed stdout to stderr
    # during serving, so any stray prints can't corrupt the stream. Diagnostics
    # here go to stderr explicitly.
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Load the graph and serve MCP over stdio."""
    graph = _load_graph()
    if not graph:
        sys.stderr.write(
            "Constellation MCP: No graph.json found. Run the Constellation engine first "
            "or set CONSTELLATION_GRAPH.\n"
        )
        # Still run — tool calls return a helpful error (see on_call_tool).

    sys.stderr.write(
        f"Constellation MCP: Loaded graph "
        f"({len(graph.get('repos', []))} repos, "
        f"{len(graph.get('entry_points', []))} entry points)\n"
    )

    server = build_server(graph)
    asyncio.run(_serve(server))


if __name__ == "__main__":
    main()
