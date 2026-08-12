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
from pydantic import BaseModel

# ── Setup ──────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
GRAPH_FILE = BASE_DIR / "output" / "graph.json"
FRONTEND_DIR = BASE_DIR / "web"

# Auth / UA used by the LLM proxy. ``_API_TOKEN`` gates protected endpoints
# (defined here so the references throughout the module resolve).
_API_TOKEN = os.environ.get("CONSTELLATION_API_TOKEN", "")
_USER_AGENT = os.environ.get("CONSTELLATION_USER_AGENT", "Constellation/0.1")

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

PROJECT_STORE = ProjectStore(BASE_DIR)
# Import a pre-multi-project graph.json (e.g. produced by start.sh) as a
# "Default" project so the app isn't empty on first load.
PROJECT_STORE.ensure_legacy_seed()

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
    repos = PROJECT_STORE.check_updates(pid)
    return {
        "repos": repos,
        "total": len(repos),
        "stale_count": sum(1 for r in repos if r["stale"]),
    }


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


class ChatMessage(BaseModel):
    """A single message in a conversation."""
    role: str = "user"  # "user" | "assistant" | "system"
    content: str = ""


class ChatRequest(BaseModel):
    """Request body for the conversational chat endpoint."""
    entry_point_id: str = ""
    node: dict = {}  # the selected call_tree node
    messages: list[ChatMessage] = []  # conversation history (user/assistant only)
    model: str = ""
    repo: str = ""  # optional repo context (for solar/flow views)
    flow_context: dict = {}  # optional flow context (name, repos, hops, etc.)


def _build_ai_context(graph: dict, entry_point_id: str, node: dict) -> tuple[str, str]:
    """
    Build the structured system prompt and fetch source code.

    Returns (system_prompt, source_content).

    If entry_point_id is empty or not found, builds a global context
    for cross-repo / architecture-level questions instead.
    """
    from engine.context_builder import ContextBuilder

    cb = ContextBuilder(graph)

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
                full_messages.append(msg)  # add assistant's tool-call message

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

            # No tool calls — extract the text response
            text = msg.get("content", "")
            return {"available": True, "response": text.strip() if text else ""}

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


def _build_chat_prompt(req, graph: dict) -> str:
    """Build the system prompt for a chat request (incl. flow/repo context)."""
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

    return system_prompt


def _truncate_json(obj, limit: int = 900) -> str:
    """JSON-encode an object, truncated for UI display."""
    s = json.dumps(obj, default=str)
    if len(s) <= limit:
        return s
    return s[:limit] + "…"


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
            full_messages.append({
                "role": "assistant",
                "content": content_acc or None,
                "tool_calls": tool_calls,
            })
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
    system_prompt = _build_chat_prompt(req, graph)

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
    system_prompt = _build_chat_prompt(req, graph)
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
