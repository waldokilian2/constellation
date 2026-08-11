"""
Graph Tools — the core query layer for Constellation's graph data.

These are pure functions that operate on the graph.json dict.
They are shared by three consumers:
  1. The MCP server (for external coding agents like Claude Code, Cursor)
  2. The REST API (/api/tools/* for debugging and manual use)
  3. The web AI tool-loop (the chat endpoint calls these as tool functions)

Each tool takes the graph dict + arguments, returns a JSON-serializable result.
No I/O, no state, no side effects — pure data in, data out.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Optional
import fnmatch
import re


# ── Shared domain constants ───────────────────────────────────────
# Broker-consumer entry kinds cross_repo.py links on (the .value form the
# graph dict carries — see EntryPointType / MESSAGE_CONSUMER_TYPES in
# cross_repo.py). Shared by get_channel_flow and find_orphans so the two
# never drift apart.
_MESSAGE_CONSUMER_TYPES = frozenset({
    "kafka-consumer",
    "rabbitmq-consumer",
    "jms-consumer",
    "sqs-consumer",
    "event-listener",
})


def _repo_of(id_str: str) -> str:
    """Extract the repo from a graph ID ('repo:Class.method').

    Mirrors the repo extraction in cross_repo.py and list_channels
    (``pid.split(':')[0]``), with a safe fallback for malformed IDs.
    """
    if not isinstance(id_str, str) or not id_str:
        return "unknown"
    return id_str.split(":")[0] if ":" in id_str else id_str


# ── Tool definitions (name, description, parameter schema) ────────
# This is the single source of truth for tool metadata. It's used by:
# - The MCP server to register tools
# - The web AI to declare available tools
# - The REST API for documentation

TOOL_DEFINITIONS = [
    {
        "name": "search_code",
        "description": (
            "Search for code across all repos. Finds entry points, functions, "
            "and files matching a query. Use this when you need to find where "
            "something is defined, who handles a specific message, or where a "
            "particular pattern appears.\n\n"
            "Examples:\n"
            '  search_code("OrderService")\n'
            '  search_code("kafka", type="kafka-consumer")\n'
            '  search_code("user", search_type="files")'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (matches method names, class names, channels, files)"},
                "type": {"type": "string", "enum": ["rest-endpoint", "kafka-consumer", "rabbitmq-consumer", "event-listener", "jms-consumer", "sqs-consumer"], "description": "Optional: filter by entry point type"},
                "search_type": {"type": "string", "enum": ["entry_points", "producers", "files", "all"], "description": "What to search. Default: all"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_node",
        "description": (
            "Get detailed information about a specific entry point or call node, "
            "including its full call tree, metrics, and source code location.\n\n"
            "Use this after search_code to drill into a specific result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entry_point_id": {"type": "string", "description": "The entry point ID (e.g. 'order-service:OrderService.createOrder')"},
            },
            "required": ["entry_point_id"],
        },
    },
    {
        "name": "find_callers",
        "description": (
            "Find all entry points that eventually call a given function or method. "
            "Useful for impact analysis: 'if I change this function, what breaks?'\n\n"
            "Traverses the full call tree across all entry points."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "method_name": {"type": "string", "description": "The method name to find callers of (e.g. 'save', 'OrderRepository.save')"},
            },
            "required": ["method_name"],
        },
    },
    {
        "name": "trace_path",
        "description": (
            "Trace the execution path between two functions. Shows the chain of "
            "calls from a source method to a target method, if one exists.\n\n"
            "Use this to answer 'how does data get from A to B?'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_method": {"type": "string", "description": "Starting method name"},
                "to_method": {"type": "string", "description": "Target method name"},
            },
            "required": ["from_method", "to_method"],
        },
    },
    {
        "name": "get_channel_flow",
        "description": (
            "Get the full flow of messages through a specific queue/topic/event channel. "
            "Shows which services produce to it and which services consume from it, "
            "with the specific methods on each side.\n\n"
            "Use this to understand cross-service communication."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "The queue/topic/event name (e.g. 'order-events')"},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "list_channels",
        "description": (
            "List all message channels connecting services, with producer and consumer "
            "repos. Use this to get an overview of inter-service communication."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_source",
        "description": (
            "Get the source code for a specific file and line range. "
            "Returns line-numbered source with optional line highlighting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file (from graph data)"},
                "start_line": {"type": "integer", "description": "Starting line number (1-indexed). Default: 1"},
                "end_line": {"type": "integer", "description": "Ending line number. Default: full file"},
                "highlight_line": {"type": "integer", "description": "Line to highlight"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "get_architecture_overview",
        "description": (
            "Get a high-level overview of the entire system: repos, entry points "
            "by type, message channels, and complexity metrics. Use this as a "
            "starting point when you need to understand the whole system."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "find_orphans",
        "description": (
            "Find message channels that are only half-wired: producers with no "
            "consumer and consumers with no producer. These often indicate a "
            "dead contract, a misnamed queue/topic, or a service not yet added "
            "to the project — though a channel may also be a legitimate boundary "
            "to an external system. Use this to spot unconnected services.\n\n"
            "Returns orphan_producers, orphan_consumers, and a summary of counts "
            "+ the affected channels. Message channels only (HTTP calls are "
            "resolved separately)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "find_cycles",
        "description": (
            "Find repo-level dependency cycles (e.g. A -> B -> A) formed by "
            "cross-repo channel edges — an architectural smell. Each cycle is "
            "returned as an ordered list of repos plus the channels involved. "
            "Use this to detect circular dependencies between services."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "find_dead_code",
        "description": (
            "Find possible dead code: methods no entry point can reach (classic "
            "dead code), thin/no-op handlers whose call tree resolved nothing, "
            "and repos isolated from all cross-repo links. Unreachable-method "
            "detection needs a graph generated by a recent engine (it precomputes "
            "full call-graph reachability); older graphs still report thin "
            "handlers and isolated repos. Use this to find unused code."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


def get_tool_definitions() -> list[dict]:
    """Return the tool definitions for LLM function-calling."""
    return TOOL_DEFINITIONS


# ── Helper ────────────────────────────────────────────────────────

def _node_display_name(node: dict) -> str:
    method = node.get("method", "")
    class_name = node.get("class_name", "")
    if class_name and method and "." not in method:
        return f"{class_name}.{method}"
    return method or class_name or "unknown"


def _walk_tree(node: dict | None):
    """Yield all nodes in a call tree depth-first."""
    if not node:
        return
    yield node
    for child in node.get("children", []):
        yield from _walk_tree(child)


def _match_method(name: str, query: str) -> bool:
    """Case-insensitive substring match on method/class names."""
    return query.lower() in name.lower()


# ── Tool implementations ──────────────────────────────────────────

def search_code(
    graph: dict,
    query: str,
    type: str = "",
    search_type: str = "all",
) -> dict:
    """
    Search for entry points, producers, and files matching a query.
    """
    query_lower = query.lower()
    results = {"entry_points": [], "producers": [], "files": []}

    # Search entry points
    if search_type in ("entry_points", "all"):
        for ep in graph.get("entry_points", []):
            if type and ep.get("type") != type:
                continue
            # Match on method, class, channel, id
            searchable = " ".join([
                ep.get("method", ""), ep.get("class_name", ""),
                ep.get("channel", ""), ep.get("id", ""),
                ep.get("message_type", ""),
            ])
            if query_lower in searchable.lower():
                results["entry_points"].append({
                    "id": ep["id"],
                    "repo": ep["repo"],
                    "type": ep["type"],
                    "channel": ep["channel"],
                    "method": ep["method"],
                    "class_name": ep["class_name"],
                    "file": ep.get("file", ""),
                    "line": ep.get("line", 0),
                    "metrics": ep.get("metrics", {}),
                })

    # Search producers
    if search_type in ("producers", "all"):
        for prod in graph.get("producers", []):
            searchable = " ".join([
                prod.get("method", ""), prod.get("channel", ""),
                prod.get("type", ""), prod.get("id", ""),
            ])
            if query_lower in searchable.lower():
                results["producers"].append(prod)

    # Search unique files (entry points, call-tree nodes, and producers)
    if search_type in ("files", "all"):
        seen_files = set()

        def consider(file: str):
            if file and file not in seen_files and query_lower in file.lower():
                seen_files.add(file)
                results["files"].append(file)

        for ep in graph.get("entry_points", []):
            consider(ep.get("file", ""))
            for node in _walk_tree(ep.get("call_tree")):
                consider(node.get("file", ""))
        for prod in graph.get("producers", []):
            consider(prod.get("file", ""))

    total = len(results["entry_points"]) + len(results["producers"]) + len(results["files"])
    results["total"] = total
    results["query"] = query
    return results


def get_node(graph: dict, entry_point_id: str) -> dict:
    """Get detailed information about a specific entry point."""
    for ep in graph.get("entry_points", []):
        if ep["id"] == entry_point_id:
            return {
                "entry_point": ep,
                "call_tree_flat": [
                    {
                        "method": n.get("method", ""),
                        "file": n.get("file", ""),
                        "line": n.get("line", 0),
                        "confidence": n.get("confidence", ""),
                        "depth": _depth_of_node(ep.get("call_tree"), n),
                    }
                    for n in _walk_tree(ep.get("call_tree"))
                ],
            }
    return {"error": f"Entry point '{entry_point_id}' not found"}


def _depth_of_node(root: dict, target: dict, depth: int = 0) -> int:
    """Find the depth of a node in a tree."""
    if not root:
        return -1
    if root is target or (
        root.get("method") == target.get("method")
        and root.get("file") == target.get("file")
        and root.get("line") == target.get("line")
    ):
        return depth
    for child in root.get("children", []):
        d = _depth_of_node(child, target, depth + 1)
        if d != -1:
            return d
    return -1


def find_callers(graph: dict, method_name: str) -> dict:
    """
    Find all entry points that call a given method.
    Traverses all call trees looking for the method name.
    """
    callers = []
    query = method_name.lower()

    for ep in graph.get("entry_points", []):
        tree = ep.get("call_tree")
        if not tree:
            continue
        # Walk the tree, find matches
        for node in _walk_tree(tree):
            node_method = node.get("method", "").lower()
            if query in node_method:
                # Found it — record the entry point and the path to this node
                path = _path_to_node(tree, node)
                callers.append({
                    "entry_point_id": ep["id"],
                    "entry_point_type": ep["type"],
                    "entry_point_channel": ep["channel"],
                    "repo": ep["repo"],
                    "called_method": node.get("method", ""),
                    "call_path": path,
                })

    return {
        "method": method_name,
        "caller_count": len(callers),
        "callers": callers,
    }


def _path_to_node(root: dict, target: dict, path: list[str] = None) -> list[str]:
    """Find the path of method names from root to target in a call tree."""
    if path is None:
        path = []
    if not root:
        return []
    current_path = path + [_node_display_name(root)]
    if root is target or (
        root.get("method") == target.get("method")
        and root.get("file") == target.get("file")
        and root.get("line") == target.get("line")
    ):
        return current_path
    for child in root.get("children", []):
        result = _path_to_node(child, target, current_path)
        if result:
            return result
    return []


def trace_path(graph: dict, from_method: str, to_method: str) -> dict:
    """
    Trace a path from one method to another through the call graph.
    Returns the chain if found.
    """
    from_lower = from_method.lower()
    to_lower = to_method.lower()
    paths_found = []

    for ep in graph.get("entry_points", []):
        tree = ep.get("call_tree")
        if not tree:
            continue
        # BFS from any node matching from_method to any node matching to_method
        chains = _bfs_chain(tree, from_lower, to_lower)
        for chain in chains:
            paths_found.append({
                "entry_point_id": ep["id"],
                "path": chain,
            })

    return {
        "from": from_method,
        "to": to_method,
        "paths_found": len(paths_found),
        "paths": paths_found,
    }


def _bfs_chain(root: dict, from_lower: str, to_lower: str) -> list[list[str]]:
    """Find chains from a node matching from_lower to a node matching to_lower."""
    chains = []

    def walk(node, in_chain, chain):
        if not node:
            return
        name = node.get("method", "")
        name_lower = name.lower()
        new_chain = chain + [name]

        if to_lower in name_lower and len(new_chain) > 1:
            chains.append(new_chain)
            return

        for child in node.get("children", []):
            walk(child, in_chain, new_chain)

    # Start from any node matching from_lower
    def find_starts(node):
        if not node:
            return
        name_lower = node.get("method", "").lower()
        if from_lower in name_lower:
            # Start a chain from here
            for child in node.get("children", []):
                walk(child, False, [node.get("method", "")])
        for child in node.get("children", []):
            find_starts(child)

    find_starts(root)
    return chains


def get_channel_flow(graph: dict, channel: str) -> dict:
    """Get the full flow of messages through a channel."""
    producers = []
    consumers = []
    channel_lower = channel.lower()

    for prod in graph.get("producers", []):
        if channel_lower in prod.get("channel", "").lower():
            producers.append({
                "id": prod["id"],
                "repo": prod["repo"],
                "method": prod["method"],
                "type": prod["type"],
                "file": prod.get("file", ""),
                "line": prod.get("line", 0),
            })

    for ep in graph.get("entry_points", []):
        ep_type = ep.get("type", "")
        if ep_type in _MESSAGE_CONSUMER_TYPES:
            if channel_lower in ep.get("channel", "").lower():
                consumers.append({
                    "id": ep["id"],
                    "repo": ep["repo"],
                    "method": ep["method"],
                    "type": ep["type"],
                    "file": ep.get("file", ""),
                    "line": ep.get("line", 0),
                })

    # Get repo-level summary
    prod_repos = sorted(set(p["repo"] for p in producers))
    cons_repos = sorted(set(c["repo"] for c in consumers))

    return {
        "channel": channel,
        "producers": producers,
        "consumers": consumers,
        "producer_repos": prod_repos,
        "consumer_repos": cons_repos,
        "is_cross_repo": len(set(prod_repos + cons_repos)) > 1,
    }


def list_channels(graph: dict) -> dict:
    """List all message channels."""
    channels = []
    seen = set()

    for link in graph.get("cross_repo_links", []):
        channel = link.get("channel", "")
        if channel in seen:
            continue
        seen.add(channel)
        prod_repos = sorted(set(
            pid.split(":")[0] for pid in link.get("producers", []) if ":" in pid
        ))
        cons_repos = sorted(set(
            cid.split(":")[0] for cid in link.get("consumers", []) if ":" in cid
        ))
        channels.append({
            "channel": channel,
            "kind": link.get("kind", "message"),
            "verb": link.get("verb", ""),
            "producer_repos": prod_repos,
            "consumer_repos": cons_repos,
            "producer_count": len(link.get("producers", [])),
            "consumer_count": len(link.get("consumers", [])),
            "is_cross_repo": len(set(prod_repos + cons_repos)) > 1,
        })

    return {"channels": channels, "total": len(channels)}


def find_orphans(graph: dict) -> dict:
    """Producers with no consumer and consumers with no producer.

    An *orphan producer* emits to a message channel no message consumer
    listens on; an *orphan consumer* listens on a message channel no message
    producer emits to. These are the two sides of a channel the cross-repo
    linker drops because it only keeps links where a producer AND consumer
    both exist (``cross_repo.py``).

    Analytical view only — reads ``entry_points`` + ``producers``, never
    consults or mutates ``cross_repo_links``.

    Scope notes:
    * **Message channels only.** ``HTTP_CALL`` producers are excluded on both
      sides: their consumers are REST endpoints resolved in the separate http
      pass (``cross_repo.py``), so comparing them against message channels
      would conflate two different channel domains.
    * **Exact channel match** (the linker keys channels exactly), not the
      substring match ``get_channel_flow`` uses for display.
    * A channel may be **legitimately** unconsumed (boundary to an external
      system). We surface the fact; we do not assert it is a bug.
    """
    sentinel = {"", "unknown", "unknown-event"}

    consumed_channels: set[str] = set()
    for ep in graph.get("entry_points", []):
        if ep.get("type") not in _MESSAGE_CONSUMER_TYPES:
            continue
        ch = (ep.get("channel") or "").strip()
        if ch not in sentinel:
            consumed_channels.add(ch)

    produced_channels: set[str] = set()
    orphan_producers: list[dict] = []
    for prod in graph.get("producers", []):
        if prod.get("type") == "http-call":
            continue  # http callers belong to the REST/path domain
        ch = (prod.get("channel") or "").strip()
        if ch in sentinel:
            continue
        produced_channels.add(ch)
        if ch not in consumed_channels:
            orphan_producers.append({
                "id": prod.get("id", ""),
                "repo": prod.get("repo", "") or _repo_of(prod.get("id")),
                "channel": ch,
                "method": prod.get("method", ""),
                "type": prod.get("type", ""),
                "file": prod.get("file", ""),
                "line": prod.get("line", 0),
            })

    orphan_consumers: list[dict] = []
    for ep in graph.get("entry_points", []):
        if ep.get("type") not in _MESSAGE_CONSUMER_TYPES:
            continue
        ch = (ep.get("channel") or "").strip()
        if ch in sentinel:
            continue
        if ch not in produced_channels:
            orphan_consumers.append({
                "id": ep.get("id", ""),
                "repo": ep.get("repo", ""),
                "channel": ch,
                "method": ep.get("method", ""),
                "type": ep.get("type", ""),
                "file": ep.get("file", ""),
                "line": ep.get("line", 0),
            })

    orphan_channels = sorted(
        {p["channel"] for p in orphan_producers}
        | {c["channel"] for c in orphan_consumers}
    )

    return {
        "orphan_producers": orphan_producers,
        "orphan_consumers": orphan_consumers,
        "summary": {
            "orphan_producers": len(orphan_producers),
            "orphan_consumers": len(orphan_consumers),
            "orphan_channels": orphan_channels,
        },
    }


def find_cycles(graph: dict) -> dict:
    """Repo-level dependency cycles through channel edges (A -> B -> A).

    Builds a directed graph ``producer-repo -> consumer-repo`` from each
    ``cross_repo_link`` (message and http alike — both are real inter-repo
    dependencies), then enumerates the elementary (simple) cycles via DFS.

    Pure read over ``cross_repo_links``; never mutates the graph.
    """
    # adjacency: adj[a][b] = set of channels on the a -> b edge
    adj: dict[str, dict[str, set[str]]] = {}
    for link in graph.get("cross_repo_links", []):
        channels = {link.get("channel", "")}
        prod_repos = {_repo_of(pid) for pid in link.get("producers", [])}
        cons_repos = {_repo_of(cid) for cid in link.get("consumers", [])}
        for pr in prod_repos:
            for cr in cons_repos:
                if pr == cr:
                    continue  # self-loop is not a cross-repo cycle
                adj.setdefault(pr, {}).setdefault(cr, set()).update(channels)

    # Stable node order; index anchoring makes each cycle surface exactly once
    # (discovered from its lowest-indexed member).
    nodes = sorted(adj.keys() | {n for nbrs in adj.values() for n in nbrs})
    index = {repo: i for i, repo in enumerate(nodes)}

    cycles: list[dict] = []
    seen: set[tuple] = set()

    def channels_along(path: list[str]) -> list[str]:
        chans: set[str] = set()
        for i in range(len(path) - 1):
            chans |= adj.get(path[i], {}).get(path[i + 1], set())
        return sorted(chans)

    def dfs(start: str, cur: str, path: list[str], on_path: set[str]) -> None:
        for nxt in adj.get(cur, {}):
            if index[nxt] < index[start]:
                continue  # only traverse nodes >= start — anchors each cycle once
            if nxt == start:
                closed = path + [start]  # e.g. ["a", "b", "a"]
                sig = tuple(closed)
                if sig not in seen:
                    seen.add(sig)
                    cycles.append({
                        "repos": closed,
                        "length": len(closed) - 1,
                        "channels": channels_along(closed),
                    })
            elif nxt not in on_path:
                on_path.add(nxt)
                dfs(start, nxt, path + [nxt], on_path)
                on_path.discard(nxt)

    for start in nodes:
        dfs(start, start, [start], {start})

    repos_in_cycles = sorted({r for c in cycles for r in c["repos"]})
    return {
        "cycles": cycles,
        "summary": {
            "cycle_count": len(cycles),
            "repos_in_cycles": repos_in_cycles,
        },
    }


def find_dead_code(graph: dict) -> dict:
    """Possible dead code: unreachable methods, thin handlers, isolated repos.

    Three signals:

    * **unreachable_methods** — methods defined in the codebase that no entry
      point can reach (classic dead code). Pre-computed by the engine via a
      full, unbounded call-graph reachability walk and stored on the graph.
      Old graphs without this field yield an empty list (and
      ``method_index_available: false``).
    * **thin_handlers** — entry points whose call tree resolved nothing beyond
      themselves (``metrics.total_nodes <= 1``); suspicious no-op handlers.
    * **isolated_repos** — repos appearing in zero cross-repo links.

    Pure read; never mutates the graph. "Possible" — surface, don't assert.
    """
    unreachable = graph.get("unreachable_methods", []) or []
    methods_total = graph.get("methods_total", 0) or 0

    thin_handlers: list[dict] = []
    for ep in graph.get("entry_points", []):
        metrics = ep.get("metrics") or {}
        # "thin" is engine-computed: a genuine no-op (method body has no
        # non-trivial calls). Fall back to the node-count heuristic for graphs
        # generated before that flag existed.
        is_thin = metrics.get("thin")
        if is_thin is None:
            is_thin = metrics.get("total_nodes", 1) <= 1
        if is_thin:
            thin_handlers.append({
                "id": ep.get("id", ""),
                "repo": ep.get("repo", ""),
                "type": ep.get("type", ""),
                "channel": ep.get("channel", ""),
                "method": ep.get("method", ""),
                "file": ep.get("file", ""),
                "line": ep.get("line", 0),
            })

    linked_repos: set[str] = set()
    for link in graph.get("cross_repo_links", []):
        for pid in link.get("producers", []):
            linked_repos.add(_repo_of(pid))
        for cid in link.get("consumers", []):
            linked_repos.add(_repo_of(cid))
    isolated_repos = [r for r in graph.get("repos", []) if r not in linked_repos]

    return {
        "unreachable_methods": unreachable,
        "thin_handlers": thin_handlers,
        "isolated_repos": isolated_repos,
        "summary": {
            "unreachable_methods": len(unreachable),
            "methods_total": methods_total,
            "thin_handlers": len(thin_handlers),
            "isolated_repos": len(isolated_repos),
            # False when the graph predates the reachability phase — callers can
            # tell the user to regenerate the graph for unreachable-method data.
            "method_index_available": "unreachable_methods" in graph,
        },
    }


def get_source(
    graph: dict,
    file_path: str,
    start_line: int = 1,
    end_line: int = 0,
    highlight_line: int = 0,
) -> dict:
    """Get source code for a file with line numbers.

    Reads are confined to the repo roots recorded in the graph. Legacy
    graphs without recorded roots fall back to a direct ``Path`` lookup.
    """
    try:
        from engine.paths import get_repo_roots, resolve_source_path

        resolved = resolve_source_path(graph, file_path)
        if resolved:
            p = resolved
        elif not get_repo_roots(graph):
            p = Path(file_path)  # legacy graph without roots — best effort
        else:
            p = None  # requested path lies outside known repo roots — deny
        if p is None or not p.exists():
            return {"error": f"File not found: {file_path}", "content": ""}
        content = p.read_text(errors="replace")
        lines = content.splitlines()
        if not end_line:
            end_line = len(lines)
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        result_lines = []
        for i in range(start, end):
            ln = i + 1
            result_lines.append({
                "line": ln,
                "content": lines[i],
                "highlighted": ln == highlight_line,
            })
        return {
            "file": str(p),
            "total_lines": len(lines),
            "showing": f"lines {start+1}-{end}",
            "lines": result_lines,
        }
    except Exception as e:
        return {"error": str(e)}


def get_architecture_overview(graph: dict) -> dict:
    """Get a high-level overview of the entire system."""
    repos = graph.get("repos", [])
    entry_points = graph.get("entry_points", [])
    producers = graph.get("producers", [])
    links = graph.get("cross_repo_links", [])

    # Count by type
    type_counts: dict[str, int] = {}
    total_complexity = {"depth": 0, "nodes": 0, "files": 0}
    for ep in entry_points:
        t = ep.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        m = ep.get("metrics", {})
        total_complexity["depth"] = max(total_complexity["depth"], m.get("depth", 0))
        total_complexity["nodes"] += m.get("total_nodes", 0)
        total_complexity["files"] += m.get("unique_files", 0)

    # Per-repo summary
    repo_summary = {}
    for ep in entry_points:
        repo = ep["repo"]
        if repo not in repo_summary:
            repo_summary[repo] = {"entry_points": 0, "producers": 0, "types": []}
        repo_summary[repo]["entry_points"] += 1
    for prod in producers:
        repo = prod["repo"]
        if repo not in repo_summary:
            repo_summary[repo] = {"entry_points": 0, "producers": 0, "types": []}
        repo_summary[repo]["producers"] += 1

    return {
        "total_repos": len(repos),
        "repos": repos,
        "total_entry_points": len(entry_points),
        "entry_points_by_type": type_counts,
        "total_producers": len(producers),
        "total_channels": len(links),
        "max_depth": total_complexity["depth"],
        "total_call_nodes": total_complexity["nodes"],
        "repo_summary": repo_summary,
        "channels": [
            {
                "channel": link["channel"],
                "producers": len(link.get("producers", [])),
                "consumers": len(link.get("consumers", [])),
            }
            for link in links
        ],
    }


# ── Tool dispatcher ───────────────────────────────────────────────

def execute_tool(graph: dict, tool_name: str, arguments: dict) -> dict:
    """
    Execute a tool by name with the given arguments.
    Returns the tool result dict, or an error dict.
    """
    dispatch = {
        "search_code": lambda args: search_code(graph, **args),
        "get_node": lambda args: get_node(graph, **args),
        "find_callers": lambda args: find_callers(graph, **args),
        "trace_path": lambda args: trace_path(graph, **args),
        "get_channel_flow": lambda args: get_channel_flow(graph, **args),
        "list_channels": lambda args: list_channels(graph),
        "find_orphans": lambda args: find_orphans(graph),
        "find_cycles": lambda args: find_cycles(graph),
        "find_dead_code": lambda args: find_dead_code(graph),
        "get_source": lambda args: get_source(graph, **args),
        "get_architecture_overview": lambda args: get_architecture_overview(graph),
    }

    handler = dispatch.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}. Available: {list(dispatch.keys())}"}

    args = _filter_args(tool_name, arguments)
    try:
        return handler(args)
    except TypeError as e:
        return {"error": f"Invalid arguments for '{tool_name}': {e}"}
    except Exception as e:
        return {"error": f"Tool '{tool_name}' failed: {e}"}


def _filter_args(tool_name: str, arguments: dict) -> dict:
    """Keep only arguments declared in the tool's parameter schema."""
    if not isinstance(arguments, dict):
        return {}
    schema = next((t for t in TOOL_DEFINITIONS if t.get("name") == tool_name), None)
    if not schema:
        return arguments
    props = (schema.get("parameters") or {}).get("properties", {})
    return {k: v for k, v in arguments.items() if k in props}
