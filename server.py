"""
Constellation API Server.

Serves the graph.json data and optionally proxies AI requests.
Also serves the static frontend.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import os
import sys
import urllib.request

# Load .env file if it exists (before importing app code)
_env_file = Path(__file__).parent.resolve() / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Setup ──────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
GRAPH_FILE = BASE_DIR / "output" / "graph.json"
FRONTEND_DIR = BASE_DIR / "web"

app = FastAPI(title="Constellation API", version="0.1.0")


# ── Graph endpoints ────────────────────────────────────────────────

def _load_graph() -> dict:
    """Load the graph.json file."""
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
async def get_graph():
    """Return the full graph data."""
    return _load_graph()


@app.get("/api/graph/entry-points")
async def get_entry_points():
    """Return all entry points."""
    graph = _load_graph()
    return graph.get("entry_points", [])


@app.get("/api/graph/entry-point/{entry_id:path}")
async def get_entry_point(entry_id: str):
    """Return a single entry point by ID."""
    graph = _load_graph()
    for ep in graph.get("entry_points", []):
        if ep["id"] == entry_id:
            return ep
    raise HTTPException(status_code=404, detail=f"Entry point '{entry_id}' not found")


@app.get("/api/graph/cross-repo-links")
async def get_cross_repo_links():
    """Return cross-repo connections."""
    graph = _load_graph()
    return graph.get("cross_repo_links", [])


@app.get("/api/graph/repos")
async def get_repos():
    """Return repo summary info."""
    graph = _load_graph()
    repos = {}
    for ep in graph.get("entry_points", []):
        repo = ep["repo"]
        if repo not in repos:
            repos[repo] = {"name": repo, "entry_points": 0, "producers": 0}
        repos[repo]["entry_points"] += 1
    for prod in graph.get("producers", []):
        repo = prod["repo"]
        if repo not in repos:
            repos[repo] = {"name": repo, "entry_points": 0, "producers": 0}
        repos[repo]["producers"] += 1
    return list(repos.values())


@app.get("/api/source")
async def get_source(file_path: str):
    """Read a source file and return its contents (for detail panel)."""
    p = _resolve_source_path(file_path)
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


def _resolve_source_path(file_path: str) -> Optional[Path]:
    """Resolve a graph file path to a real file on disk, within repo roots.

    Reads are confined to the repo roots recorded in the graph (or the
    built-in test-repo roots for legacy graphs), which prevents arbitrary
    file reads via the API.
    """
    from engine.paths import resolve_source_path
    try:
        graph = _load_graph()
    except HTTPException:
        graph = {}
    return resolve_source_path(graph, file_path, fallback_roots=_legacy_repo_roots(graph))


def _legacy_repo_roots(graph: dict) -> list[Path]:
    """Roots for graphs recorded before repo_roots existed (test repos)."""
    return [BASE_DIR / "tests" / "repos" / repo for repo in graph.get("repos", [])]


@app.post("/api/analyze")
async def analyze_repos(repos: list[str] = Body(...), request: Request = None):
    """Re-run the engine on new repo paths and return the graph."""
    _require_auth(request)

    from engine.constellation import ConstellationEngine
    from pathlib import Path as P

    repo_dirs = []
    for repo_path in repos:
        p = P(repo_path).resolve()
        if not p.exists():
            raise HTTPException(status_code=400, detail=f"Path not found: {p}")
        repo_dirs.append((p.name, p))

    engine = ConstellationEngine()
    graph = await run_in_threadpool(engine.analyze, repo_dirs)

    # Save to disk
    output_path = BASE_DIR / "output" / "graph.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(graph.to_json())

    return graph.to_dict()


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
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if not api_key:
        return {
            "available": False,
            "message": "AI features require an API key. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your environment.",
        }

    model = model or os.environ.get("OPENAI_MODEL", "nemotron-3-ultra-free")
    is_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))

    try:
        if is_anthropic:
            # Anthropic — tool use not implemented in this path yet,
            # fall through to simple completion
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
            }
            body = json.dumps({
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 800,
                "system": system_prompt,
                "messages": messages,
            }).encode()
            req_obj = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req_obj, timeout=90) as resp:
                data = json.loads(resp.read().decode())
            text = data.get("content", [{}])[0].get("text", "")
            return {"available": True, "response": text.strip()}

        # ── OpenAI-compatible with tool-use loop ───────────────
        from engine.graph_tools import execute_tool

        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        url = base + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _USER_AGENT,
        }

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
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        yield {"type": "error", "message": "AI features require an API key. Set OPENAI_API_KEY in the environment."}
        return

    if os.environ.get("ANTHROPIC_API_KEY"):
        yield {"type": "error", "message": "Streaming with tool-use requires the OpenAI-compatible provider (OPENAI_BASE_URL)."}
        return

    from engine.graph_tools import execute_tool

    model = model or os.environ.get("OPENAI_MODEL", "nemotron-3-ultra-free")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    url = base + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": _USER_AGENT,
    }
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


@app.post("/api/ai/explain")
async def ai_explain(req: AIRequest):
    """
    Legacy single-call endpoint. Kept for backwards compatibility.
    The /api/ai/chat endpoint is preferred.
    """
    graph = _load_graph()
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


@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest):
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

    graph = _load_graph()
    system_prompt = _build_chat_prompt(req, graph)

    # Convert messages to plain dicts
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    # Pass graph tools so the AI can search and explore
    tools = get_tool_definitions()
    result = _call_llm(system_prompt, messages, req.model, tools=tools, graph=graph)
    return result


@app.post("/api/ai/chat/stream")
async def ai_chat_stream(req: ChatRequest):
    """
    Streaming variant of /api/ai/chat.

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

    graph = _load_graph()
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
    """Return the list of available free models for the model dropdown."""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"available": False, "models": FREE_MODELS}

    try:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        req = urllib.request.Request(
            base + "/models",
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": _USER_AGENT},
            method="GET",
        )
        data = await run_in_threadpool(_fetch_models_json, base, api_key)
        free_models = [m for m in all_models if m and m.endswith("-free")]
        models = free_models or all_models or FREE_MODELS
        return {"available": True, "models": models}
    except Exception:
        return {"available": False, "models": FREE_MODELS}


# ── Graph tools REST endpoints ────────────────────────────────────
# These expose the same tools that the MCP server and web AI use,
# but as simple HTTP endpoints for debugging, manual use, or external
# integrations that don't speak MCP.

from engine.graph_tools import get_tool_definitions, execute_tool

@app.get("/api/tools")
async def list_tools():
    """List all available graph tools and their schemas."""
    return {"tools": get_tool_definitions()}


@app.post("/api/tools/{tool_name}")
async def call_tool(tool_name: str, args: dict = Body(default={})):
    """Execute a graph tool by name with JSON arguments."""
    graph = _load_graph()
    result = execute_tool(graph, tool_name, args)
    return result


# Convenience GET wrappers for common queries

@app.get("/api/tools/search")
async def tool_search(q: str, type: str = "", search_type: str = "all"):
    """Quick search: GET /api/tools/search?q=OrderService"""
    graph = _load_graph()
    return execute_tool(graph, "search_code", {"query": q, "type": type, "search_type": search_type})


@app.get("/api/tools/callers")
async def tool_callers(method: str):
    """Quick find_callers: GET /api/tools/callers?method=save"""
    graph = _load_graph()
    return execute_tool(graph, "find_callers", {"method_name": method})


@app.get("/api/tools/channels")
async def tool_channels():
    """List all message channels."""
    graph = _load_graph()
    return execute_tool(graph, "list_channels", {})


@app.get("/api/tools/channel/{channel_name}")
async def tool_channel(channel_name: str):
    """Get flow for a specific channel."""
    graph = _load_graph()
    return execute_tool(graph, "get_channel_flow", {"channel": channel_name})


@app.get("/api/tools/overview")
async def tool_overview():
    """Get architecture overview."""
    graph = _load_graph()
    return execute_tool(graph, "get_architecture_overview", {})


@app.get("/api/tools/trace")
async def tool_trace(from_method: str, to_method: str):
    """Trace path: GET /api/tools/trace?from_method=createOrder&to_method=save"""
    graph = _load_graph()
    return execute_tool(graph, "trace_path", {"from_method": from_method, "to_method": to_method})


# ── Static frontend ────────────────────────────────────────────────

# Mount static files FIRST so they take priority over the catch-all route
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Prevent stale bundle caching: dev iterates on app.js/styles.css constantly,
    and browsers hold onto old files when there's no Cache-Control header."""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
async def index():
    """Serve the main frontend page."""
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    return JSONResponse({
        "message": "Constellation API is running. Frontend not found.",
        "hint": f"Put your frontend in {FRONTEND_DIR}/",
        "api_docs": "/docs"
    })


@app.get("/health")
async def health():
    return {"status": "ok"}
