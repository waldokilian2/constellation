#!/usr/bin/env python3
"""
Constellation MCP Server.

Exposes the Constellation graph tools as an MCP (Model Context Protocol)
server over stdio. This lets external coding agents (Claude Code, Cursor,
etc.) query the codebase graph that Constellation extracted.

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
import json
import os
import sys
from pathlib import Path

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


# ── MCP Protocol (stdio JSON-RPC 2.0) ─────────────────────────────

def _send(msg: dict):
    """Send a JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _respond(id_val: int | str | None, result: dict):
    """Send a successful response."""
    _send({"jsonrpc": "2.0", "id": id_val, "result": result})


def _respond_error(id_val: int | str | None, code: int, message: str):
    """Send an error response."""
    _send({"jsonrpc": "2.0", "id": id_val, "error": {"code": code, "message": message}})


def main():
    """Main MCP server loop — reads JSON-RPC from stdin, writes to stdout."""
    # Import tool definitions and executor
    from engine.graph_tools import TOOL_DEFINITIONS, execute_tool

    graph = _load_graph()
    if not graph:
        sys.stderr.write(
            "Constellation MCP: No graph.json found. Run the Constellation engine first "
            "or set CONSTELLATION_GRAPH.\n"
        )
        # Still run — we'll return helpful errors on tool calls

    sys.stderr.write(
        f"Constellation MCP: Loaded graph "
        f"({len(graph.get('repos', []))} repos, "
        f"{len(graph.get('entry_points', []))} entry points)\n"
    )

    initialized = False

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        # ── Handle requests (have an id) ────────────────────────
        if method == "initialize":
            _respond(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "constellation",
                    "version": "0.1.0",
                },
            })
            initialized = True
            continue

        if method == "initialized":
            # Notification — no response needed
            continue

        if method == "tools/list":
            _respond(msg_id, {"tools": TOOL_DEFINITIONS})
            continue

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if not graph:
                _respond(msg_id, {
                    "content": [{
                        "type": "text",
                        "text": "Error: No graph.json loaded. Run the Constellation engine first "
                                "or set CONSTELLATION_GRAPH environment variable.",
                    }],
                    "isError": True,
                })
                continue

            result = execute_tool(graph, tool_name, tool_args)

            # Format the result as MCP content
            text = json.dumps(result, indent=2, default=str)
            _respond(msg_id, {
                "content": [{"type": "text", "text": text}],
                "isError": bool(result.get("error")),
            })
            continue

        if method == "resources/list":
            # Expose graph.json as a resource
            _respond(msg_id, {
                "resources": [
                    {
                        "uri": "constellation://graph",
                        "name": "Constellation Graph",
                        "description": "The full codebase graph extracted by Constellation",
                        "mimeType": "application/json",
                    }
                ]
            })
            continue

        if method == "resources/read":
            uri = params.get("uri", "")
            if uri == "constellation://graph":
                _respond(msg_id, {
                    "contents": [{
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(graph, indent=2, default=str),
                    }]
                })
                continue
            _respond_error(msg_id, -32602, f"Unknown resource: {uri}")
            continue

        # Unknown method
        if msg_id is not None:
            _respond_error(msg_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
