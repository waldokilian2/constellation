"""
Constellation API Server.

Serves the graph.json data and optionally proxies AI requests.
Also serves the static frontend.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import asyncio
import json
import os
import queue
import re
import sys
import threading
import urllib.request

# Load .env file if it exists (before importing app code)
_env_file = Path(__file__).parent.resolve() / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ── Setup ──────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
GRAPH_FILE = BASE_DIR / "output" / "graph.json"
FRONTEND_DIR = BASE_DIR / "web"

# Auth / UA used by the LLM proxy. ``_API_TOKEN`` gates protected endpoints
# (defined here so the references throughout the module resolve).
_API_TOKEN = os.environ.get("CONSTELLATION_API_TOKEN", "")
_USER_AGENT = os.environ.get("CONSTELLATION_USER_AGENT", "Constellation/0.1")

# Event-loop policy: on Windows the default proactor loop closes the listening
# socket on a transient accept error (CPython #93821), making the server
# unreachable. We force the selector loop there (the default elsewhere), which
# doesn't have the bug. Imported only on Windows — the selector loop is already
# the default on Linux/macOS so there is nothing to do there. Set before
# uvicorn's loop exists.
if sys.platform == "win32":
    import win_accept_resilience

    win_accept_resilience.install()

# ── AI provider config (OpenAI-compatible; Zen by default) ─────────
# Zen (https://opencode.ai/zen) exposes an OpenAI-compatible API. Any
# OpenAI-compatible gateway works by overriding the *_BASE_URL vars.
# OPENCODE_* vars are canonical; OPENAI_* are accepted as aliases.
ZEN_BASE_URL = "https://opencode.ai/zen/v1"
ZEN_DEFAULT_MODEL = "deepseek-v4-flash-free"


def _ai_api_key() -> str:
    """API key for the OpenAI-compatible provider (Zen by default)."""
    return os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def _ai_base_url() -> str:
    """Provider base URL, normalized to end in ``/v1``."""
    base = (
        os.environ.get("OPENCODE_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ZEN_BASE_URL
    ).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def _ai_model(model: str = "") -> str:
    """Resolve the model id, honoring per-request overrides."""
    return (
        model
        or os.environ.get("OPENCODE_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or ZEN_DEFAULT_MODEL
    )


from engine import git_hosts
from engine.project_store import ProjectStore
from engine.conversation_store import ConversationStore, Conversation

PROJECT_STORE = ProjectStore(BASE_DIR)
# Import a pre-multi-project graph.json (e.g. produced by start.sh) as a
# "Default" project so the app isn't empty on first load.
PROJECT_STORE.ensure_legacy_seed()

# Conversation persistence (multi-turn chat history). Project-scoped, like
# ProjectStore, so each project's conversations live under its own directory.
CONVERSATION_STORE = ConversationStore(BASE_DIR)

app = FastAPI(title="Constellation API", version="0.1.0")

# ── MCP over Streamable HTTP ───────────────────────────────────────
# The same graph tools the MCP stdio server (`python -m engine.mcp_server`)
# exposes are also served at /mcp over Streamable HTTP, so the docker
# container — which runs this FastAPI app — can serve MCP to clients with
# just a URL ("type": "http", "url": "http://localhost:8765/mcp") and no
# stdio subprocess. Backed by the ProjectStore, so one server exposes every
# project (list_projects tool + optional `project` arg on each tool). Local
# stdio usage via .mcp.json is unchanged. MCP is optional here — if the SDK
# is missing, the web app keeps running.
try:
    from engine.mcp_server import mount_streamable_http
    mount_streamable_http(app, store=PROJECT_STORE)
except Exception as e:
    sys.stderr.write(f"Constellation MCP: /mcp not mounted: {e}\n")


# ── Graph + project endpoints ──────────────────────────────────────

def _load_project(pid: str) -> dict:
    """Load a project metadata record, 404 if unknown."""
    meta = PROJECT_STORE.get_project(pid)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Project '{pid}' not found")
    return meta


def _load_graph(pid: Optional[str] = None) -> dict:
    """Load a project's graph.

    With ``pid`` → that project's ``graph.json`` (404 if not analysed yet).
    Without ``pid`` → the legacy global ``output/graph.json`` (MCP/compat).
    """
    if pid:
        meta = _load_project(pid)
        try:
            return PROJECT_STORE.load_graph(pid)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail=f"Project '{meta['name']}' has no graph yet (status: {meta.get('status', '?')})",
            )
    if not GRAPH_FILE.exists():
        raise HTTPException(status_code=404, detail="graph.json not found. Run the engine first.")
    with open(GRAPH_FILE) as f:
        return json.load(f)


def _require_auth(request: Request) -> None:
    """Reject the request unless it presents the configured bearer token.

    No-op when CONSTELLATION_API_TOKEN is unset (open local tool).
    """
    if not _API_TOKEN:
        return
    if request is None or request.headers.get("Authorization") != f"Bearer {_API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/api/graph")
async def get_graph_legacy():
    """Legacy global graph — returns the first ready project's graph (MCP/compat)."""
    for p in PROJECT_STORE.list_projects():
        if p.get("status") == "ready":
            try:
                return PROJECT_STORE.load_graph(p["id"])
            except FileNotFoundError:
                continue
    return _load_graph(None)


@app.get("/api/projects")
async def list_projects():
    """List all projects (newest first)."""
    return {"projects": PROJECT_STORE.list_projects()}


@app.get("/api/projects/{pid}")
async def get_project(pid: str):
    """Return one project's metadata."""
    return _load_project(pid)


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    """Delete a project and its cloned repos + graph."""
    if not PROJECT_STORE.delete(pid):
        raise HTTPException(status_code=404, detail=f"Project '{pid}' not found")
    return {"ok": True, "id": pid}


@app.get("/api/projects/{pid}/graph")
async def get_project_graph(pid: str):
    """Return the full graph for a project."""
    return _load_graph(pid)


@app.get("/api/projects/{pid}/source")
async def get_project_source(pid: str, file_path: str):
    """Read a source file within a project's repo roots (for the detail panel)."""
    graph = _load_graph(pid)
    p = _resolve_source_path(file_path, graph)
    if p is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    try:
        content = p.read_text(errors="replace")
        lines = content.splitlines()
        return {
            "file": str(p),
            "content": content,
            "lines": lines,
            "line_count": len(lines),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _resolve_source_path(file_path: str, graph: Optional[dict] = None) -> Optional[Path]:
    """Resolve a graph file path to a real file on disk, within repo roots.

    Reads are confined to the repo roots recorded in the graph (or the
    built-in test-repo roots for legacy graphs), which prevents arbitrary
    file reads via the API.
    """
    from engine.paths import resolve_source_path
    if graph is None:
        try:
            graph = _load_graph(None)
        except HTTPException:
            graph = {}
    return resolve_source_path(graph, file_path, fallback_roots=_legacy_repo_roots(graph))


def _legacy_repo_roots(graph: dict) -> list[Path]:
    """Roots for graphs recorded before repo_roots existed (test repos)."""
    return [BASE_DIR / "tests" / "repos" / repo for repo in graph.get("repos", [])]


# ── Project ingestion (git clone + engine run, streamed over SSE) ───


class ProjectIngestRequest(BaseModel):
    """Create a new project from a set of git URLs."""
    name: str
    repos: list[str] = []


class RepoIngestRequest(BaseModel):
    """Add repos (git URLs) to an existing project (re-analyses the union)."""
    repos: list[str]


def _classify_log(line: str) -> str:
    """Map an engine/clone log line to a coarse phase for the UI."""
    low = line.lower()
    if low.startswith("[clone]"):
        return "clone"
    if low.startswith("[scan]"):
        return "scan"
    if low.startswith("[link]"):
        return "link"
    if low.startswith("[done]"):
        return "done"
    if low.startswith("[graph]"):
        return "graph"
    return "info"


@app.post("/api/projects")
async def create_project(req: ProjectIngestRequest):
    """Create a project (name + git URLs) and stream the ingestion logs.

    Returns an SSE stream:
      {"type": "log", "phase": "...", "message": "..."} — progress line
      {"type": "done", "project": {...}}                  — finished ok
      {"type": "error", "message": "..."}                 — failed
    """
    if not req.repos:
        raise HTTPException(status_code=400, detail="At least one repository URL is required")
    meta = PROJECT_STORE.create_meta(req.name)
    return _ingest_response(meta["id"], req.repos)


@app.post("/api/projects/{pid}/repos")
async def add_project_repos(pid: str, req: RepoIngestRequest):
    """Add git-URL repos to an existing project; re-analyse and stream logs."""
    _load_project(pid)
    if not req.repos:
        raise HTTPException(status_code=400, detail="At least one repository URL is required")
    # Mark the project busy so the UI can reflect re-analysis in progress.
    PROJECT_STORE.mark_status(pid, "analyzing")
    return _ingest_response(pid, req.repos)


def _ingest_response(pid: str, urls: list[str]):
    return _stream_response(_ingest_stream(pid, urls))


def _stream_response(gen):
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse_run(pid: str, produce):
    """Run a project job on a worker thread, surfacing events as SSE.

    ``produce(log)`` does the work (clone/scan/analyse) on a background
    thread, calling ``log(msg)`` for each progress line, and returns the
    updated project metadata. Emits the same event schema the UI already
    consumes for ingestion: ``log`` / ``done`` / ``error``.
    """
    q: "queue.Queue" = queue.Queue()
    SENTINEL = object()

    def _worker():
        try:
            def _log(msg: str):
                q.put({"type": "log", "phase": _classify_log(msg), "message": msg})
            meta = produce(_log)
            q.put({"type": "done", "project": meta})
        except Exception as e:
            PROJECT_STORE.mark_error(pid, str(e))
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(SENTINEL)

    threading.Thread(target=_worker, daemon=True).start()
    loop = asyncio.get_running_loop()
    while True:
        ev = await loop.run_in_executor(None, q.get)
        if ev is SENTINEL:
            break
        yield "data: " + json.dumps(ev) + "\n\n"


async def _ingest_stream(pid: str, urls: list[str]):
    """Run project ingestion on a worker thread, surfacing events as SSE."""
    produce = lambda log: PROJECT_STORE.ingest(pid, list(urls), log=log)
    async for chunk in _sse_run(pid, produce):
        yield chunk


@app.post("/api/projects/{pid}/rescan")
async def rescan_project(pid: str, pull: bool = False):
    """Re-analyse a project's existing clones and stream the logs.

    With ``pull=false`` (default) this re-extracts the graph from the *current*
    on-disk source with no network — the right call after the engine logic
    changes. With ``pull=true`` it first re-fetches any repos whose remote HEAD
    has moved (shallow re-clone of stale repos only), then re-analyses.
    """
    _load_project(pid)
    PROJECT_STORE.mark_status(pid, "analyzing")

    def produce(log):
        if pull:
            log("[clone] Syncing repositories to latest…")
            PROJECT_STORE.pull_repos(pid, only_stale=True, log=log)
        log("[scan] Re-scanning repositories…")
        return PROJECT_STORE.analyze_project(pid, log=log)

    return _stream_response(_sse_run(pid, produce))


@app.get("/api/projects/{pid}/updates")
async def project_updates(pid: str):
    """Check whether any tracked repo's remote has moved (no download).

    Returns ``{repos: [...], stale_count, total}`` where each repo entry is
    ``{name, source, current_commit, remote_commit, stale, error}``. Cheap to
    poll (``git ls-remote``, no checkout). Local-seed repos are excluded.
    """
    _load_project(pid)
    repos = await run_in_threadpool(PROJECT_STORE.check_updates, pid)
    return {
        "repos": repos,
        "total": len(repos),
        "stale_count": sum(1 for r in repos if r["stale"]),
    }


# ── Boards (external board sync via MCP) ────────────────────────────
# Connect a GitHub board (via the official GitHub MCP server) to a project,
# pull its items, and refresh on demand. Two-way write-back (Phase 2) and
# graph linking (Phase 3) extend these routes.

from engine.boards import provider_for, BoardError
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _board_identity(provider: str, config: dict) -> dict:
    """Stable id / display name / source URL for a board derived from its config."""
    if provider == "github-mcp":
        owner = (config or {}).get("owner", "")
        project_number = (config or {}).get("project_number") or (config or {}).get("project")
        if project_number:
            num = str(project_number)
            return {
                "id": f"github-project:{owner}/{num}",
                "name": f"{owner} project #{num}",
                "source_url": f"https://github.com/users/{owner}/projects/{num}" if owner else "",
                "kind": "project",
            }
        repo = (config or {}).get("repo", "")
        scope = f"{owner}/{repo}".strip("/")
        return {
            "id": f"github:{scope}",
            "name": f"{scope} issues",
            "source_url": f"https://github.com/{scope}" if scope else "",
            "kind": "issues",
        }
    return {"id": provider, "name": provider, "source_url": "", "kind": "issues"}


class BoardConnectRequest(BaseModel):
    """Connect an external board to a project."""
    provider: str            # "github-mcp"
    config: dict = {}        # e.g. {"owner": "...", "repo": "..."}
    name: str = ""           # optional display-name override


@app.get("/api/projects/{pid}/boards")
async def list_boards(pid: str):
    """Connected boards + their cached items for a project."""
    _load_project(pid)
    return PROJECT_STORE.load_boards(pid)


@app.post("/api/projects/{pid}/boards")
async def connect_board(pid: str, req: BoardConnectRequest):
    """Connect a board and run its initial sync (pulls items now)."""
    _load_project(pid)
    ident = _board_identity(req.provider, req.config)
    board = {
        "id": ident["id"],
        "provider": req.provider,
        "name": req.name or ident["name"],
        "kind": ident["kind"],
        "source_url": ident["source_url"],
        "config": req.config,
        "items": [],
        "synced_at": "",
    }
    try:
        provider = provider_for(board)
        # Pre-flight token check so a missing/bad token fails with the API's
        # details before any MCP call is made.
        token_status = await run_in_threadpool(provider.token_status)
        if not token_status.get("valid"):
            raise BoardError(token_status.get("error") or "GitHub token not valid", status=401)
        # For project boards, use the real GitHub project title as the display name.
        if req.name:
            board["name_override"] = True  # keep a user-provided name on later syncs
        elif ident["kind"] == "project":
            title = await provider.project_title(board)
            if title:
                board["name"] = title
        items = await provider.list_items(board)
        options = await provider.status_options(board)
        caps = await provider.capabilities(board)
    except BoardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    board["items"] = [it.to_dict() for it in items]
    if options:
        board["status_options"] = options
    if caps:
        board["capabilities"] = caps
    board["synced_at"] = _now_iso()

    data = PROJECT_STORE.load_boards(pid)
    data["boards"] = [b for b in data["boards"] if b["id"] != board["id"]]  # replace if present
    data["boards"].append(board)
    PROJECT_STORE.save_boards(pid, data)
    return {"board": board, "count": len(board["items"])}


@app.post("/api/projects/{pid}/boards/{bid:path}/sync")
async def sync_board(pid: str, bid: str):
    """Refresh a board's items from its source (pull)."""
    # Board ids contain '/', e.g. "github:owner/repo". A plain {bid} segment
    # cannot match a value with a slash (uvicorn decodes %2F -> '/' before
    # route matching, so even the encoded URL misses and returns 404). The
    # ":path" converter matches across slashes and is already percent-decoded.
    _load_project(pid)
    data = PROJECT_STORE.load_boards(pid)
    board = next((b for b in data["boards"] if b["id"] == bid), None)
    if board is None:
        raise HTTPException(status_code=404, detail=f"Board '{bid}' not found")
    try:
        provider = provider_for(board)
        items = await provider.list_items(board)
        options = await provider.status_options(board)
        caps = await provider.capabilities(board)
        # Refresh the real GitHub project title on sync (unless the user set a
        # custom name) so boards connected before the title feature get renamed.
        if board.get("kind") == "project" and not board.get("name_override"):
            title = await provider.project_title(board)
            if title:
                board["name"] = title
    except BoardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    board["items"] = [it.to_dict() for it in items]
    if options:
        board["status_options"] = options
    if caps:
        board["capabilities"] = caps
    board["synced_at"] = _now_iso()
    PROJECT_STORE.save_boards(pid, data)
    return {"board": board, "count": len(board["items"])}


@app.delete("/api/projects/{pid}/boards/{bid:path}")
async def disconnect_board(pid: str, bid: str):
    """Remove a connected board (cached items are deleted; the source is untouched)."""
    # Same ":path" converter as sync_board — bid ids contain '/' so a plain
    # {bid} segment can never match.
    _load_project(pid)
    data = PROJECT_STORE.load_boards(pid)
    before = len(data["boards"])
    data["boards"] = [b for b in data["boards"] if b["id"] != bid]
    if len(data["boards"]) == before:
        raise HTTPException(status_code=404, detail=f"Board '{bid}' not found")
    PROJECT_STORE.save_boards(pid, data)
    return {"ok": True, "id": bid}


class BoardItemPatch(BaseModel):
    """Move a board item (item_id in body — item ids contain '/', like board ids)."""
    item_id: str
    status: str = ""


class BoardCommentRequest(BaseModel):
    item_id: str
    body: str


def _replace_item(board: dict, item: dict) -> None:
    """Update a cached item in place (matched by id)."""
    items = board.get("items") or []
    for i, it in enumerate(items):
        if it.get("id") == item.get("id"):
            items[i] = item
            return
    items.append(item)
    board["items"] = items


@app.post("/api/projects/{pid}/boards/{bid:path}/items")
async def update_board_item(pid: str, bid: str, req: BoardItemPatch):
    """Move a board item — immediate write to the source.

    For project boards this is the card's Status (swim lane); for issues boards,
    the open/closed state. Note: the GitHub MCP server does not expose field-level
    etags, so there's no optimistic-concurrency conflict detection here — the
    write is last-writer-wins, and the canonical item is refetched and returned.
    """
    _load_project(pid)
    data = PROJECT_STORE.load_boards(pid)
    board = next((b for b in data["boards"] if b["id"] == bid), None)
    if board is None:
        raise HTTPException(status_code=404, detail=f"Board '{bid}' not found")
    try:
        provider = provider_for(board)
        item = await provider.update_item(board, req.item_id, {"status": req.status})
    except BoardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    _replace_item(board, item.to_dict())
    PROJECT_STORE.save_boards(pid, data)
    return {"item": item.to_dict()}


@app.post("/api/projects/{pid}/boards/{bid:path}/items/comment")
async def comment_board_item(pid: str, bid: str, req: BoardCommentRequest):
    """Add a comment to a board item's underlying issue."""
    _load_project(pid)
    data = PROJECT_STORE.load_boards(pid)
    board = next((b for b in data["boards"] if b["id"] == bid), None)
    if board is None:
        raise HTTPException(status_code=404, detail=f"Board '{bid}' not found")
    try:
        provider = provider_for(board)
        await provider.add_comment(board, req.item_id, req.body)
    except BoardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"ok": True, "item_id": req.item_id}


# ── Git-host import (remote repo discovery) ────────────────────────

@app.get("/api/remotes/repos")
async def remote_repos(link: str = ""):
    """Resolve an org/workspace/team-project link into its repository list.

    Supports github.com, gitlab.com, bitbucket.org and dev.azure.com
    (public repos; GitHub additionally uses ``gh auth token`` / a
    ``GITHUB_TOKEN`` env var when available). Only the provider's fixed
    API base is contacted — ``link`` is used solely to extract a
    validated owner identifier.
    """
    if not (link or "").strip():
        raise HTTPException(status_code=400, detail="A git-host link is required")
    try:
        return git_hosts.fetch_repos(link.strip(), token=git_hosts.github_token())
    except git_hosts.GitHostError as e:
        raise HTTPException(status_code=e.status if e.status else 400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to reach the git host API")


# ── AI proxy endpoints ─────────────────────────────────────────────

class AIRequest(BaseModel):
    """Request body for AI explanation (legacy single-call endpoint)."""
    function_source: str = ""
    function_name: str = ""
    context: str = ""
    question: str = "Explain this function in 2-3 sentences."
    model: str = ""


class ToolCallFunction(BaseModel):
    """OpenAI-format tool-call function payload."""
    name: str = ""
    arguments: str = ""  # JSON-encoded string


class ToolCall(BaseModel):
    """A single assistant tool call."""
    id: str = ""
    type: str = "function"
    function: ToolCallFunction = Field(default_factory=ToolCallFunction)


class ChatMessage(BaseModel):
    """A single message in a conversation (OpenAI chat-completions format).

    ``tool_calls`` is present on ``assistant`` messages that requested tool
    execution; ``tool_call_id`` + ``name`` identify the tool result on
    ``tool``-role messages. All optional so legacy clients that only send
    ``role``/``content`` keep working.
    """
    role: str = "user"  # "user" | "assistant" | "system" | "tool"
    content: str = ""
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_openai_dict(self) -> dict:
        """Serialize to the dict shape the LLM API expects.

        Drops empty ``content`` when tool_calls are present (the OpenAI
        spec wants ``content: null`` there) and omits the optional tool
        fields entirely when unset.
        """
        d: dict = {"role": self.role}
        if self.tool_calls:
            d["content"] = self.content if self.content else None
            d["tool_calls"] = [
                {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ]
        else:
            d["content"] = self.content
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d


class ChatRequest(BaseModel):
    """Request body for the conversational chat endpoint (stateless variant)."""
    entry_point_id: str = ""
    node: dict = {}  # the selected call_tree node
    messages: list[ChatMessage] = []  # conversation history (user/assistant only)
    model: str = ""
    repo: str = ""  # optional repo context (for solar/flow views)
    flow_context: dict = {}  # optional flow context (name, repos, hops, etc.)
    planner: bool = False  # when True, uses the change-planner system prompt
    boards: bool = False  # when True, uses the board-focused system prompt (Boards view)
    conversation_id: str = ""  # optional: target a persisted conversation


class ConversationCreateRequest(BaseModel):
    """Create a new conversation."""
    title: str = ""
    is_default: bool = False
    # Which surface owns this conversation: "chat" (per-page assistant) or
    # "planner" (AI Change Planner). Defaults to "chat" for back-compat.
    kind: str = "chat"


class ConversationChatRequest(BaseModel):
    """Send a message into a persisted conversation (streaming endpoint).

    The context fields (``entry_point_id``, ``node``, ``repo``,
    ``flow_context``, ``planner``) shape the system prompt for *this* turn
    only; the conversation itself carries the full message history (incl.
    tool calls/results) across turns.
    """
    content: str = ""
    model: str = ""
    entry_point_id: str = ""
    node: dict = {}
    repo: str = ""
    flow_context: dict = {}
    planner: bool = False
    boards: bool = False  # board-focused system prompt (Boards view)


def _build_ai_context(graph: dict, entry_point_id: str, node: dict) -> tuple[str, str]:
    """
    Build the structured system prompt and fetch source code.

    Returns (system_prompt, source_content).

    If entry_point_id is empty or not found, builds a global context
    for cross-repo / architecture-level questions instead.
    """
    from engine.context_builder import ContextBuilder

    boards = PROJECT_STORE.load_boards(pid).get("boards", [])
    cb = ContextBuilder(graph, boards=boards)

    # Global mode — no specific entry point selected
    if not entry_point_id:
        return (cb.build_global_prompt(), "")

    # Find the entry point
    entry_point = None
    for ep in graph.get("entry_points", []):
        if ep["id"] == entry_point_id:
            entry_point = ep
            break

    if not entry_point:
        # Fallback to global context
        return (cb.build_global_prompt(), "")

    # Fetch source if available
    source_content = ""
    source_line_count = 0
    file_path = node.get("file", "") if node else ""
    if file_path:
        resolved = _resolve_source_path(file_path)
        if resolved and resolved.exists():
            try:
                source_content = resolved.read_text(errors="replace")
                source_line_count = len(source_content.splitlines())
            except Exception:
                pass

    system_prompt = cb.build_system_prompt(
        entry_point, node or {}, source_content, source_line_count
    )
    return (system_prompt, source_content)


def _call_llm(
    system_prompt: str,
    messages: list[dict],
    model: str = "",
    tools: list[dict] = None,
    graph: dict = None,
) -> dict:
    """
    Call the configured LLM API. Returns {available, response} or {available: False, ...}.

    If tools and graph are provided, runs a tool-use loop:
    the LLM can call tools, we execute them, feed results back, repeat.
    Supports OpenAI function-calling format.
    """
    api_key = _ai_api_key()
    if not api_key:
        return {
            "available": False,
            "message": "AI features require an API key. Set OPENCODE_API_KEY (or OPENAI_API_KEY) in your .env / environment.",
        }

    try:
        # ── OpenAI-compatible (Zen by default) with tool-use loop ──
        from engine.graph_tools import execute_tool

        url = _ai_base_url() + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _USER_AGENT,
        }
        model = _ai_model(model)

        # Build messages with system prompt prepended
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        # OpenAI function schema
        oai_tools = None
        if tools:
            oai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    }
                }
                for t in tools
            ]

        # Tool-use loop (max 5 iterations to prevent infinite loops)
        for _iteration in range(5):
            body_dict = {
                "model": model,
                "messages": full_messages,
                "max_tokens": 800,
            }
            if oai_tools:
                body_dict["tools"] = oai_tools

            body = json.dumps(body_dict).encode()
            req_obj = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req_obj, timeout=90) as resp:
                data = json.loads(resp.read().decode())

            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})

            # If the model wants to call tools, execute them
            tool_calls = msg.get("tool_calls", [])
            if tool_calls and graph is not None:
                # Append the assistant's tool-call message, minus reasoning-only
                # fields (non-standard on the OpenAI wire format).
                full_messages.append({
                    k: v for k, v in msg.items()
                    if k not in ("reasoning_content", "reasoning")
                })

                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    try:
                        tool_args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        tool_args = {}

                    tool_result = execute_tool(graph, tool_name, tool_args)

                    full_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": tool_name,
                        "content": json.dumps(tool_result, default=str),
                    })
                # Continue loop — let the model process tool results
                continue

            # No tool calls — extract the text response (and any reasoning)
            text = msg.get("content", "")
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            return {
                "available": True,
                "response": text.strip() if text else "",
                "reasoning": reasoning,
            }

        # Exceeded max iterations — return what we have
        return {"available": True, "response": text.strip() if text else "(tool loop limit reached)"}

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()[:500]
        except Exception:
            pass
        return {"available": False, "error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        return {"available": False, "error": str(e)}


def _build_chat_prompt(req, graph: dict, pid: str = "") -> str:
    """Build the system prompt for a chat request (incl. flow/repo context)."""
    if req.planner:
        from engine.context_builder import ContextBuilder
        boards = PROJECT_STORE.load_boards(pid).get("boards", []) if pid else []
        cb = ContextBuilder(graph, boards=boards)
        return cb.build_planner_prompt(req.repo or "")
    if getattr(req, "boards", False):
        from engine.context_builder import ContextBuilder
        boards = PROJECT_STORE.load_boards(pid).get("boards", []) if pid else []
        cb = ContextBuilder(graph, boards=boards)
        return cb.build_boards_prompt()

    system_prompt, _ = _build_ai_context(graph, req.entry_point_id, req.node)

    if req.flow_context:
        fc = req.flow_context
        flow_lines = [
            "",
            "## CURRENT FLOW CONTEXT",
            f"You are being asked about the flow: **{fc.get('name', 'unknown')}**",
        ]
        if fc.get("repos"):
            flow_lines.append(f"Repos involved: {', '.join(fc['repos'])}")
        if fc.get("repo_count"):
            flow_lines.append(f"Total repos: {fc['repo_count']}")
        if fc.get("hop_count") is not None:
            flow_lines.append(f"Hops: {fc['hop_count']}")
        if fc.get("origin_type"):
            origin = "REST endpoint" if fc["origin_type"] == "rest" else "external event"
            flow_lines.append(f"Origin: {fc.get('origin_label', origin)} ({origin})")
        flow_lines.append("The user is viewing this flow in the codebase mapper. Answer with the flow's cross-repo event chain in mind.")
        system_prompt += "\n" + "\n".join(flow_lines)

    if req.repo and not req.entry_point_id:
        system_prompt += f"\n\n## CURRENT REPO CONTEXT\nThe user is viewing the **{req.repo}** service. Focus your answers on this service."

    # Reinforce the task_complete tool so the AI explicitly signals when it's
    # done (rather than just stopping). The server auto-continues on 'incomplete'.
    system_prompt += (
        "\n\n## COMPLETION\n"
        "When you have fully answered the user's request, call the `task_complete` "
        "tool with status 'complete' and a brief summary. If you have more steps to "
        "execute and want to checkpoint progress first, call it with status "
        "'incomplete' and describe the remaining next_steps."
    )

    return system_prompt


def _truncate_json(obj, limit: int = 900) -> str:
    """JSON-encode an object, truncated for UI display."""
    s = json.dumps(obj, default=str)
    if len(s) <= limit:
        return s
    return s[:limit] + "…"


def _append_diagram_context(system_prompt: str, pid: str, cid: str, planner: bool) -> str:
    """Tell the planner what's currently in the plan-preview panel.

    Only appended in planner mode. The AI uses this to decide whether to
    ``add``, ``replace`` (by id), or ``remove`` via the ``render_diagram``
    tool. Non-planner chats have no preview panel, so they're untouched.
    """
    if not planner or not pid or not cid:
        return system_prompt
    diags = CONVERSATION_STORE.get_diagrams(pid, cid)
    if diags:
        lines = [
            "",
            "## PLAN-PREVIEW PANEL (current state)",
            "The right-side panel currently shows these diagrams:",
        ]
        for d in diags:
            lines.append(f"- id `{d.get('id')}` — {d.get('header')} ({d.get('kind')})")
        lines.append(
            "Use render_diagram 'replace' (with the id) to edit one of these, "
            "'remove' (with the id) to delete one, or 'add' for a new diagram. "
            "Don't duplicate a diagram already shown unless you're replacing it."
        )
        return system_prompt + "\n" + "\n".join(lines)
    return system_prompt + (
        "\n\n## PLAN-PREVIEW PANEL\n"
        "The right-side panel is empty. Use the render_diagram tool (action "
        "'add') to show diagrams there — Mermaid flows are preferred."
    )


def _delta_reasoning(delta: dict) -> str:
    """Pull reasoning tokens out of a streamed delta.

    Reasoning-style models (DeepSeek's ``reasoning_content``, Qwen's
    ``reasoning``) stream their chain-of-thought in a separate delta field
    rather than ``content``. Without reading it, those tokens are silently
    dropped — never shown and never persisted.
    """
    return delta.get("reasoning_content") or delta.get("reasoning") or ""


def _strip_reasoning(messages: list[dict]) -> list[dict]:
    """Return a copy of ``messages`` with UI-side ``reasoning`` fields removed.

    ``reasoning`` is stored on assistant messages so the UI can render it, but
    it is not part of the OpenAI wire format — strip it before sending history
    back to the LLM.
    """
    out = []
    for m in messages:
        m = dict(m)
        m.pop("reasoning", None)
        out.append(m)
    return out


def _stream_llm_events(
    system_prompt: str,
    messages: list[dict],
    model: str = "",
    tools: list[dict] = None,
    graph: dict = None,
):
    """
    Generator yielding SSE event dicts for a streaming chat with tool use.

    Events:
      {"type": "token",       "text": "..."}        — streamed text delta
      {"type": "tool_start",  "name": ..., "args": {...}}   — AI requested a tool
      {"type": "tool_result", "name": ..., "result": "..."} — tool executed (truncated)
      {"type": "done"}                                  — stream complete
      {"type": "error", "message": "..."}               — failure

    The AI can call tools mid-stream: token deltas stream first, then a
    tool_start/tool_result pair appears, then streaming resumes with the
    final answer.
    """
    api_key = _ai_api_key()
    if not api_key:
        yield {"type": "error", "message": "AI features require an API key. Set OPENCODE_API_KEY (or OPENAI_API_KEY) in your .env / environment."}
        return

    from engine.graph_tools import execute_tool

    url = _ai_base_url() + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": _USER_AGENT,
    }
    model = _ai_model(model)
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    oai_tools = None
    if tools:
        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    for _iteration in range(5):
        body_dict = {
            "model": model,
            "messages": full_messages,
            "max_tokens": 1200,
            "stream": True,
        }
        if oai_tools:
            body_dict["tools"] = oai_tools

        body = json.dumps(body_dict).encode()
        req_obj = urllib.request.Request(url, data=body, headers=headers, method="POST")

        tool_acc: dict[int, dict] = {}
        content_acc = ""
        reasoning_acc = ""
        finish = None

        try:
            with urllib.request.urlopen(req_obj, timeout=180) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        finish = "stop"
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    c = delta.get("content")
                    if c:
                        content_acc += c
                        yield {"type": "token", "text": c}
                    r = _delta_reasoning(delta)
                    if r:
                        reasoning_acc += r
                        yield {"type": "reasoning", "text": r}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            acc["name"] += fn["name"]
                        if fn.get("arguments"):
                            acc["arguments"] += fn["arguments"]
                    fr = choices[0].get("finish_reason")
                    if fr:
                        finish = fr
                        if fr in ("stop", "tool_calls"):
                            break
        except urllib.error.HTTPError as e:
            yield {"type": "error", "message": f"HTTP {e.code}: {e.read().decode()[:300]}"}
            return
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        # The AI asked to call tools — execute and loop for the final answer
        if tool_acc and graph is not None:
            tool_calls = []
            for idx in sorted(tool_acc):
                acc = tool_acc[idx]
                tool_calls.append({
                    "id": acc["id"],
                    "type": "function",
                    "function": {"name": acc["name"], "arguments": acc["arguments"]},
                })
            assistant_msg = {
                "role": "assistant",
                "content": content_acc or None,
                "tool_calls": tool_calls,
            }
            if reasoning_acc:
                assistant_msg["reasoning"] = reasoning_acc
            full_messages.append(assistant_msg)
            for tc in tool_calls:
                fn = tc["function"]
                tool_name = fn["name"]
                try:
                    tool_args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_args = {}
                yield {"type": "tool_start", "name": tool_name, "args": tool_args}
                tool_result = execute_tool(graph, tool_name, tool_args)
                yield {"type": "tool_result", "name": tool_name, "result": _truncate_json(tool_result)}
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tool_name,
                    "content": json.dumps(tool_result, default=str),
                })
            continue
        elif tool_acc:
            yield {"type": "error", "message": "Tool use requested but no graph available."}
            return

        # No tools — the stream is complete
        yield {"type": "done"}
        return

    yield {"type": "done"}


def _stream_llm_events_v2(
    system_prompt: str,
    full_messages: list[dict],
    model: str = "",
    tools: list[dict] = None,
    graph: dict = None,
    max_iterations: int = 20,
    pid: str = "",
    cid: str = "",
):
    """
    Generator yielding SSE event dicts for a multi-turn streaming chat with
    tool use, full tool-history, and ``task_complete`` auto-continuation.

    Mutates ``full_messages`` in place: each iteration appends the assistant
    message (with ``tool_calls``) and the corresponding ``tool``-role result
    messages, so when the generator returns the caller can persist
    ``full_messages`` (minus the leading system prompt) as the conversation's
    authoritative history.

    Events (superset of ``_stream_llm_events``):
      {"type": "token",       "text": "..."}
      {"type": "tool_start",  "name", "args": {...}}
      {"type": "tool_result", "name", "result": "..."}        — truncated for UI
      {"type": "tool_result", "name": "render_diagram", "result", "diagrams"}
                                                              — FULL + panel state
      {"type": "task_complete", "status", "summary", "next_steps"}
      {"type": "done"}
      {"type": "error", "message"}

    Differences from v1:
      * 20 iterations (was 5) and 4096 max_tokens (was 1200) — enough runway
        for the planner to explore + produce a full plan.
      * Full tool history across iterations is preserved in ``full_messages``.
      * ``task_complete`` with status ``complete`` ends the stream; ``incomplete``
        injects a system nudge and continues.
      * ``render_diagram`` (planner-only) is executed against the persisted
        conversation (needs ``pid``/``cid``) and emits the full, un-truncated
        result plus the current panel ``diagrams`` for the frontend to mirror.
    """
    api_key = _ai_api_key()
    if not api_key:
        yield {"type": "error", "message": "AI features require an API key. Set OPENCODE_API_KEY (or OPENAI_API_KEY) in your .env / environment."}
        return

    from engine.graph_tools import execute_tool
    from engine.boards.tools import BOARD_TOOL_NAMES, execute_board_tool

    url = _ai_base_url() + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": _USER_AGENT,
    }
    model = _ai_model(model)

    oai_tools = None
    if tools:
        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    for _iteration in range(max_iterations):
        # Signal the start of a new assistant message (one per LLM iteration).
        # The frontend uses this to open a fresh chat segment, so a multi-step
        # turn renders as separate bubbles: [tool chips] … [final answer].
        yield {"type": "message_start"}

        body_dict = {
            "model": model,
            "messages": full_messages,
            "max_tokens": 4096,
            "stream": True,
        }
        if oai_tools:
            body_dict["tools"] = oai_tools

        body = json.dumps(body_dict).encode()
        req_obj = urllib.request.Request(url, data=body, headers=headers, method="POST")

        tool_acc: dict[int, dict] = {}
        content_acc = ""
        reasoning_acc = ""
        finish = None

        try:
            with urllib.request.urlopen(req_obj, timeout=180) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        finish = "stop"
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    c = delta.get("content")
                    if c:
                        content_acc += c
                        yield {"type": "token", "text": c}
                    r = _delta_reasoning(delta)
                    if r:
                        reasoning_acc += r
                        yield {"type": "reasoning", "text": r}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            acc["name"] += fn["name"]
                        if fn.get("arguments"):
                            acc["arguments"] += fn["arguments"]
                    fr = choices[0].get("finish_reason")
                    if fr:
                        finish = fr
                        if fr in ("stop", "tool_calls"):
                            break
        except urllib.error.HTTPError as e:
            yield {"type": "error", "message": f"HTTP {e.code}: {e.read().decode()[:300]}"}
            return
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        # The AI asked to call tools — execute them and loop for the final answer
        if tool_acc and graph is not None:
            tool_calls = []
            for idx in sorted(tool_acc):
                acc = tool_acc[idx]
                tool_calls.append({
                    "id": acc["id"],
                    "type": "function",
                    "function": {"name": acc["name"], "arguments": acc["arguments"]},
                })
            # Append the assistant's tool-call message to the running history.
            assistant_msg = {
                "role": "assistant",
                "content": content_acc or None,
                "tool_calls": tool_calls,
            }
            if reasoning_acc:
                assistant_msg["reasoning"] = reasoning_acc
            full_messages.append(assistant_msg)

            has_task_complete = any(
                tc["function"]["name"] == "task_complete" for tc in tool_calls
            )

            tc_result_by_name: list[tuple[str, dict]] = []

            for tc in tool_calls:
                fn = tc["function"]
                tool_name = fn["name"]
                try:
                    tool_args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_args = {}
                yield {"type": "tool_start", "name": tool_name, "args": tool_args}

                if tool_name == "render_diagram":
                    # Planner-only, stateful UI tool. State lives on the
                    # conversation, so this is handled here (with pid/cid)
                    # rather than in the pure graph-tool dispatcher.
                    rd_action = str(tool_args.get("action", "add"))
                    rd_kind = str(tool_args.get("kind", "mermaid"))
                    rd_code = str(tool_args.get("code", ""))

                    # Validate Mermaid BEFORE storing so the AI gets the parse
                    # error in the tool result and can self-correct this turn
                    # (don't pollute the panel with an unrenderable diagram).
                    # Validation runs on the REPAIRED source (same repair the
                    # browser applies) and returns the repaired code to store —
                    # so fixable trivia like a bare edge label never triggers
                    # a rejection/retry loop; only genuinely broken syntax does.
                    rd_error = ""
                    rd_code_stored = rd_code
                    if rd_kind == "mermaid" and rd_action in ("add", "replace") and rd_code.strip():
                        try:
                            from engine.mermaid_validator import validate_mermaid
                            ok, err, repaired = validate_mermaid(rd_code)
                            if not ok:
                                rd_error = err
                            elif repaired:
                                rd_code_stored = repaired
                        except Exception as ve:  # validator infra problem → don't block
                            rd_error = ""

                    if rd_error:
                        tool_result = {
                            "action": rd_action,
                            "ok": False,
                            "error": (
                                "Mermaid failed to render, so it was NOT added "
                                "to the panel. Fix the syntax and call "
                                f"render_diagram again. Parse error: {rd_error}"
                            ),
                            "diagrams": CONVERSATION_STORE.get_diagrams(pid, cid),
                        }
                    else:
                        tool_result = CONVERSATION_STORE.mutate_diagrams(
                            pid, cid,
                            action=rd_action,
                            diagram_id=str(tool_args.get("diagram_id", "")),
                            header=str(tool_args.get("header", "")),
                            code=rd_code_stored,
                            kind=rd_kind,
                        )
                    # Diagrams can be large Mermaid — never truncate; also
                    # pass the current panel list so the frontend can mirror.
                    yield {
                        "type": "tool_result",
                        "name": tool_name,
                        "result": json.dumps(tool_result, default=str),
                        "diagrams": tool_result.get("diagrams", []),
                    }
                else:
                    if tool_name in BOARD_TOOL_NAMES and pid:
                        # Board tools read/write the synced boards via the MCP
                        # provider (async) — dispatched separately from the pure
                        # graph tools.
                        tool_result = execute_board_tool(PROJECT_STORE, pid, tool_name, tool_args)
                    else:
                        tool_result = execute_tool(graph, tool_name, tool_args)
                    if tool_name == "task_complete":
                        yield {
                            "type": "task_complete",
                            "status": tool_result.get("status", "complete"),
                            "summary": tool_result.get("summary", ""),
                            "next_steps": tool_result.get("next_steps", ""),
                        }
                    else:
                        yield {"type": "tool_result", "name": tool_name, "result": _truncate_json(tool_result)}

                tc_result_by_name.append((tool_name, tool_result))
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tool_name,
                    "content": json.dumps(tool_result, default=str),
                })

            # task_complete decides whether to stop or continue.
            if has_task_complete:
                tc_result = next(
                    (r for (n, r) in tc_result_by_name if n == "task_complete"), {}
                )
                status = tc_result.get("status", "complete")
                if status == "complete":
                    yield {"type": "done"}
                    return
                # incomplete — nudge the model to continue the remaining steps.
                next_steps = tc_result.get("next_steps") or "the remaining steps"
                full_messages.append({
                    "role": "system",
                    "content": (
                        f"Continue working. Remaining steps: {next_steps}. "
                        "Do not call task_complete again until you have completed "
                        "these steps or need to checkpoint."
                    ),
                })
                continue

            continue  # loop to let the LLM process the tool results

        elif tool_acc:
            yield {"type": "error", "message": "Tool use requested but no graph available."}
            return

        # No tools — the stream is complete. Persist the final answer so it
        # survives refresh/reload (tool-call turns are appended above, but a
        # plain text reply was previously discarded here).
        if content_acc or reasoning_acc:
            assistant_msg = {"role": "assistant", "content": content_acc}
            if reasoning_acc:
                assistant_msg["reasoning"] = reasoning_acc
            full_messages.append(assistant_msg)
        yield {"type": "done"}
        return

    # Exhausted the iteration budget — stop gracefully. (Reachable only if
    # every iteration requested tools, so the last assistant message — with
    # its tool_calls — was already appended above; nothing extra to save.)
    yield {"type": "done"}


@app.post("/api/projects/{pid}/ai/explain")
async def ai_explain(pid: str, req: AIRequest):
    """
    Legacy single-call endpoint. Kept for backwards compatibility.
    The /api/projects/{pid}/ai/chat endpoint is preferred.
    """
    graph = _load_graph(pid)
    # Try to find the entry point from the context string
    entry_point_id = ""
    context = req.context or ""
    for ep in graph.get("entry_points", []):
        if ep["id"] in context:
            entry_point_id = ep["id"]
            break

    node = {
        "method": req.function_name,
        "file": "",
        "line": 0,
    }

    system_prompt, source = _build_ai_context(graph, entry_point_id, node)

    messages = [{"role": "user", "content": req.question}]
    result = _call_llm(system_prompt, messages, req.model)

    if not result.get("available"):
        result["fallback"] = f"This is the '{req.function_name}' function. AI service unavailable."
    return result


@app.post("/api/projects/{pid}/ai/chat")
async def ai_chat(pid: str, req: ChatRequest):
    """
    Conversational chat endpoint with structured graph context and tool-use.

    The frontend sends:
    - entry_point_id: which entry point we're looking at
    - node: the selected call_tree node
    - repo: optional repo name (for solar/flow views)
    - flow_context: optional flow metadata (name, repos, hops, etc.)
    - messages: conversation history (user/assistant pairs)
    - model: optional model override

    The backend:
    1. Builds a structured system prompt from the graph data
    2. Fetches the source code for the selected node
    3. Provides the AI with graph tools (search_code, find_callers, etc.)
    4. Runs a tool-use loop: AI can call tools to explore the codebase
    5. Returns the final response
    """
    from engine.graph_tools import get_tool_definitions

    graph = _load_graph(pid)
    system_prompt = _build_chat_prompt(req, graph, pid=pid)

    # Convert messages to plain dicts
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    # Pass graph tools so the AI can search and explore
    tools = get_tool_definitions()
    result = _call_llm(system_prompt, messages, req.model, tools=tools, graph=graph)
    return result


@app.post("/api/projects/{pid}/ai/chat/stream")
async def ai_chat_stream(pid: str, req: ChatRequest):
    """
    Streaming variant of /api/projects/{pid}/ai/chat.

    Returns a Server-Sent-Events stream. Each `data:` line is JSON:
      {"type": "token", "text": "..."}        — a streamed text delta
      {"type": "tool_start", "name", "args"}  — the AI requested a tool
      {"type": "tool_result", "name", "result"} — tool result (truncated)
      {"type": "done"}
      {"type": "error", "message"}

    The frontend renders token deltas for streaming output and shows each
    tool call as a visible step chip between the text.
    """
    from engine.graph_tools import get_tool_definitions

    graph = _load_graph(pid)
    system_prompt = _build_chat_prompt(req, graph, pid=pid)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    tools = get_tool_definitions()

    _SENTINEL = object()

    async def event_stream():
        # The LLM stream is a blocking (urllib) generator. Run it on a
        # worker thread and surface events through a queue so the event
        # loop is never blocked while waiting on the provider.
        q: queue.Queue = queue.Queue()

        def _produce():
            try:
                for ev in _stream_llm_events(system_prompt, messages, req.model,
                                             tools=tools, graph=graph):
                    q.put(ev)
            except Exception as e:
                q.put({"type": "error", "message": str(e)})
            finally:
                q.put(_SENTINEL)

        threading.Thread(target=_produce, daemon=True).start()
        loop = asyncio.get_running_loop()
        while True:
            ev = await loop.run_in_executor(None, q.get)
            if ev is _SENTINEL:
                break
            yield "data: " + json.dumps(ev) + "\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Conversation endpoints ─────────────────────────────────────────


@app.post("/api/projects/{pid}/conversations")
async def create_conversation(pid: str, req: ConversationCreateRequest = ConversationCreateRequest()):
    """Create a new conversation. Body: {title?, is_default?, kind?}.

    ``kind`` ("chat" | "planner") scopes the conversation to a surface so the
    page chat and the planner keep separate histories.
    """
    _load_project(pid)
    conv = CONVERSATION_STORE.create(pid, title=req.title or "", kind=req.kind or "chat")
    return conv.meta()


@app.get("/api/projects/{pid}/conversations")
async def list_conversations(pid: str, kind: str = ""):
    """List conversations for a project (metadata only).

    ``?kind=chat|planner`` restricts the list to one surface so each chat's
    history menu only shows its own conversations.
    """
    _load_project(pid)
    return {"conversations": CONVERSATION_STORE.list(pid, kind=kind or "")}


@app.get("/api/projects/{pid}/conversations/default")
async def get_default_conversation(pid: str, kind: str = "chat"):
    """Return (or create) the default conversation for a surface, with messages.

    ``?kind=chat|planner`` selects the surface; each gets its own default.
    """
    _load_project(pid)
    conv = CONVERSATION_STORE.get_or_create_default(pid, kind=kind or "chat")
    return conv.to_dict()


@app.get("/api/projects/{pid}/conversations/{cid}")
async def get_conversation(pid: str, cid: str):
    """Get a full conversation with messages."""
    _load_project(pid)
    conv = CONVERSATION_STORE.get(pid, cid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"Conversation '{cid}' not found")
    return conv.to_dict()


@app.delete("/api/projects/{pid}/conversations/{cid}")
async def delete_conversation(pid: str, cid: str):
    """Delete a conversation."""
    _load_project(pid)
    if not CONVERSATION_STORE.delete(pid, cid):
        raise HTTPException(status_code=404, detail=f"Conversation '{cid}' not found")
    return {"ok": True, "id": cid}


@app.get("/api/projects/{pid}/conversations/{cid}/diagrams")
async def get_conversation_diagrams(pid: str, cid: str):
    """Return the diagrams currently shown in a conversation's preview panel."""
    _load_project(pid)
    if not CONVERSATION_STORE.get(pid, cid):
        raise HTTPException(status_code=404, detail=f"Conversation '{cid}' not found")
    return {"diagrams": CONVERSATION_STORE.get_diagrams(pid, cid)}


@app.delete("/api/projects/{pid}/conversations/{cid}/diagrams/{diagram_id}")
async def delete_conversation_diagram(pid: str, cid: str, diagram_id: str):
    """Remove a single diagram from the preview panel (manual × button)."""
    _load_project(pid)
    if not CONVERSATION_STORE.get(pid, cid):
        raise HTTPException(status_code=404, detail=f"Conversation '{cid}' not found")
    res = CONVERSATION_STORE.mutate_diagrams(pid, cid, action="remove", diagram_id=diagram_id)
    return {"ok": True, "diagrams": res.get("diagrams", [])}


@app.delete("/api/projects/{pid}/conversations/{cid}/diagrams")
async def clear_conversation_diagrams(pid: str, cid: str):
    """Clear every diagram from the preview panel."""
    _load_project(pid)
    if not CONVERSATION_STORE.get(pid, cid):
        raise HTTPException(status_code=404, detail=f"Conversation '{cid}' not found")
    res = CONVERSATION_STORE.mutate_diagrams(pid, cid, action="clear")
    return {"ok": True, "diagrams": res.get("diagrams", [])}


@app.post("/api/projects/{pid}/conversations/{cid}/chat/stream")
async def conversation_chat_stream(pid: str, cid: str, req: ConversationChatRequest):
    """
    Send a message into a persisted conversation and stream the response.

    The conversation carries full message history (user, assistant with
    tool_calls, tool results) across turns. The context fields on the request
    shape the system prompt for *this* turn only.
    """
    from engine.graph_tools import get_tool_definitions

    _load_project(pid)
    conv = CONVERSATION_STORE.get(pid, cid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"Conversation '{cid}' not found")

    graph = _load_graph(pid)

    system_prompt = _build_chat_prompt(req, graph, pid=pid)
    system_prompt = _append_diagram_context(system_prompt, pid, cid, req.planner)

    conv.messages.append({"role": "user", "content": req.content})

    # full_messages = system prompt + full conversation history (plain dicts).
    # System-role messages persisted from prior "continue" nudges are stripped
    # because the system prompt is rebuilt fresh each turn from view context.
    # UI-side `reasoning` fields are stripped too — not part of the wire format.
    full_messages: list[dict] = [{"role": "system", "content": system_prompt}] + _strip_reasoning([
        m for m in conv.messages if m.get("role") != "system"
    ])

    tools = get_tool_definitions(include_planner_tools=req.planner)
    if PROJECT_STORE.load_boards(pid).get("boards"):
        from engine.boards.tools import BOARD_TOOL_DEFINITIONS
        tools = tools + BOARD_TOOL_DEFINITIONS

    _SENTINEL = object()

    async def event_stream():
        q: queue.Queue = queue.Queue()

        def _produce():
            try:
                for ev in _stream_llm_events_v2(
                    system_prompt, full_messages,
                    model=req.model, tools=tools, graph=graph,
                    pid=pid, cid=cid,
                ):
                    q.put(ev)
            except Exception as e:
                q.put({"type": "error", "message": str(e)})
            finally:
                q.put(_SENTINEL)

        threading.Thread(target=_produce, daemon=True).start()
        loop = asyncio.get_running_loop()
        while True:
            ev = await loop.run_in_executor(None, q.get)
            if ev is _SENTINEL:
                break
            yield "data: " + json.dumps(ev) + "\n\n"

        # Persist the updated history. _stream_llm_events_v2 mutated
        # full_messages in place (appended assistant + tool messages); strip
        # the leading system prompt (rebuilt each turn) and save the rest.
        try:
            CONVERSATION_STORE.replace_messages(pid, cid, full_messages[1:])
        except Exception:
            pass  # persistence failure must not invalidate a successful stream

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/projects/{pid}/conversations/{cid}/chat")
async def conversation_chat(pid: str, cid: str, req: ConversationChatRequest):
    """Non-streaming variant of the conversation-scoped chat endpoint."""
    from engine.graph_tools import get_tool_definitions

    _load_project(pid)
    conv = CONVERSATION_STORE.get(pid, cid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"Conversation '{cid}' not found")

    graph = _load_graph(pid)

    system_prompt = _build_chat_prompt(req, graph, pid=pid)
    system_prompt = _append_diagram_context(system_prompt, pid, cid, req.planner)

    conv.messages.append({"role": "user", "content": req.content})

    full_messages: list[dict] = [{"role": "system", "content": system_prompt}] + _strip_reasoning([
        m for m in conv.messages if m.get("role") != "system"
    ])

    tools = get_tool_definitions(include_planner_tools=req.planner)
    if PROJECT_STORE.load_boards(pid).get("boards"):
        from engine.boards.tools import BOARD_TOOL_DEFINITIONS
        tools = tools + BOARD_TOOL_DEFINITIONS

    # Run the v2 tool loop synchronously, collecting the streamed text.
    content_parts: list[str] = []
    tool_events: list[dict] = []
    had_error = ""
    for ev in _stream_llm_events_v2(
        system_prompt, full_messages, req.model, tools=tools, graph=graph,
        pid=pid, cid=cid,
    ):
        t = ev.get("type")
        if t == "token":
            content_parts.append(ev.get("text", ""))
        elif t == "tool_result":
            tool_events.append({"name": ev.get("name"), "result": ev.get("result", "")})
        elif t == "task_complete":
            tool_events.append({"name": "task_complete", "status": ev.get("status")})
        elif t == "error":
            had_error = ev.get("message", "")
        elif t == "done":
            break

    if had_error:
        return {"available": False, "error": had_error}

    CONVERSATION_STORE.replace_messages(pid, cid, full_messages[1:])

    return {
        "available": True,
        "response": "".join(content_parts).strip(),
        "tools": tool_events,
    }


def _fetch_models_json(base: str, api_key: str) -> dict:
    """GET the provider's /models list (runs in a thread; may block)."""
    req = urllib.request.Request(
        base + "/models",
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": _USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


# Free models offered by the OpenAI-compatible provider (fallback if the
# provider's /models endpoint can't be reached).
FREE_MODELS = [
    "deepseek-v4-flash-free",
    "mimo-v2.5-free",
    "ling-3.0-flash-free",
    "nemotron-3-ultra-free",
    "north-mini-code-free",
    "laguna-s-2.1-free",
    "longcat-2.0-free",
]


@app.get("/api/ai/models")
async def ai_models():
    """
    Return the provider's free models for the chat model dropdown.

    Fetches /models and keeps ONLY the free tier (model ids ending in
    "-free"); non-free models are never selectable. If the key is missing
    or the request fails, falls back to the bundled FREE_MODELS list.
    """
    api_key = _ai_api_key()
    if not api_key:
        return {"available": False, "models": FREE_MODELS}

    try:
        base = _ai_base_url()
        data = await run_in_threadpool(_fetch_models_json, base, api_key)
        ids = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
        free_models = sorted({i for i in ids if i and i.endswith("-free")})
        return {"available": True, "models": free_models or FREE_MODELS}
    except Exception:
        return {"available": False, "models": FREE_MODELS}


# ── Graph tools REST endpoints ────────────────────────────────────
# These expose the same tools that the MCP server and web AI use,
# but as simple HTTP endpoints for debugging, manual use, or external
# integrations that don't speak MCP.

from engine.graph_tools import get_tool_definitions, execute_tool

@app.get("/api/projects/{pid}/tools")
async def list_tools(pid: str):
    """List all available graph tools and their schemas (project-scoped)."""
    return {"tools": get_tool_definitions()}


@app.post("/api/projects/{pid}/tools/{tool_name}")
async def call_tool(pid: str, tool_name: str, args: dict = Body(default={})):
    """Execute a graph tool by name with JSON arguments."""
    graph = _load_graph(pid)
    result = execute_tool(graph, tool_name, args)
    return result


# Convenience GET wrappers for common queries

@app.get("/api/projects/{pid}/tools/search")
async def tool_search(pid: str, q: str, type: str = "", search_type: str = "all"):
    """Quick search: GET /api/projects/{pid}/tools/search?q=OrderService"""
    graph = _load_graph(pid)
    return execute_tool(graph, "search_code", {"query": q, "type": type, "search_type": search_type})


@app.get("/api/projects/{pid}/tools/callers")
async def tool_callers(pid: str, method: str):
    """Quick find_callers: GET /api/projects/{pid}/tools/callers?method=save"""
    graph = _load_graph(pid)
    return execute_tool(graph, "find_callers", {"method_name": method})


@app.get("/api/projects/{pid}/tools/channels")
async def tool_channels(pid: str):
    """List all message channels."""
    graph = _load_graph(pid)
    return execute_tool(graph, "list_channels", {})


@app.get("/api/projects/{pid}/tools/channel/{channel_name}")
async def tool_channel(pid: str, channel_name: str):
    """Get flow for a specific channel."""
    graph = _load_graph(pid)
    return execute_tool(graph, "get_channel_flow", {"channel": channel_name})


@app.get("/api/projects/{pid}/tools/overview")
async def tool_overview(pid: str):
    """Get architecture overview."""
    graph = _load_graph(pid)
    return execute_tool(graph, "get_architecture_overview", {})


@app.get("/api/projects/{pid}/tools/trace")
async def tool_trace(pid: str, from_method: str, to_method: str):
    """Trace path: GET /api/projects/{pid}/tools/trace?from_method=createOrder&to_method=save"""
    graph = _load_graph(pid)
    return execute_tool(graph, "trace_path", {"from_method": from_method, "to_method": to_method})


@app.get("/api/projects/{pid}/tools/orphans")
async def tool_orphans(pid: str):
    """Producers with no consumer + consumers with no producer (message channels)."""
    graph = _load_graph(pid)
    return execute_tool(graph, "find_orphans", {})


@app.get("/api/projects/{pid}/tools/cycles")
async def tool_cycles(pid: str):
    """Repo-level dependency cycles (A->B->A via channel edges)."""
    graph = _load_graph(pid)
    return execute_tool(graph, "find_cycles", {})


@app.get("/api/projects/{pid}/tools/dead_code")
async def tool_dead_code(pid: str):
    """Possible dead code: unreachable methods, thin handlers, isolated repos."""
    graph = _load_graph(pid)
    return execute_tool(graph, "find_dead_code", {})


@app.get("/api/projects/{pid}/tools/diff")
async def tool_diff(pid: str):
    """Graph diff: what changed since the last analysis (vs the latest snapshot)."""
    graph = _load_graph(pid)
    old = PROJECT_STORE.latest_snapshot(pid)
    return execute_tool(graph, "diff_graphs", {"old_graph": old or {}})


@app.get("/api/projects/{pid}/diff")
async def project_diff(pid: str, at: str = "", light: bool = False):
    """What changed since a previous snapshot — the diff *and* the two graphs.

    ``at`` selects the snapshot to compare against (default: the latest one);
    ``light=1`` omits the graphs for cheap project-list polling. The diff
    itself always comes from the pure ``diff_graphs`` tool, so the engine
    semantics stay the single source of truth; the graphs are returned so the
    UI can render before/after details (metrics, call trees, link shapes).
    """
    graph = _load_graph(pid)
    old = None
    if at:
        old = PROJECT_STORE.load_snapshot(pid, at)
        if old is None:
            raise HTTPException(status_code=404, detail=f"Snapshot '{at}' not found")
    else:
        old = PROJECT_STORE.latest_snapshot(pid)

    result = execute_tool(graph, "diff_graphs", {"old_graph": old or {}})
    payload = {
        "diff": result,
        "snapshots": PROJECT_STORE.list_snapshots(pid),
        "compared_at": (old or {}).get("generated_at", "") or "",
    }
    if not light:
        payload["old_graph"] = old
        payload["new_graph"] = graph
    return payload


# ── Static frontend ────────────────────────────────────────────────

# Vite builds to web/dist/. In production, server.py serves the built
# bundle directly. In dev (npm run dev), Vite runs on :5173 and proxies
# API calls here — this static serving is simply unused.
DIST_DIR = FRONTEND_DIR / "dist"

if DIST_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(DIST_DIR / "assets")),
        name="assets",
    )


@app.middleware("http")
async def cache_headers(request, call_next):
    """Vite emits content-hashed filenames (/assets/index-AbC123.js) so
    built assets are safe to cache permanently. The HTML entry point and
    API responses should never be cached."""
    response = await call_next(request)
    if request.url.path.startswith("/assets"):
        # Immutable — filename changes when content changes
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.url.path == "/" or request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
async def index():
    """Serve the built frontend (Vite output in web/dist/)."""
    index_file = DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    return JSONResponse(
        {
            "message": "Constellation API is running. Frontend not built yet.",
            "hint": "Run: npm install && npm run build",
            "api_docs": "/docs",
        },
        status_code=404,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
