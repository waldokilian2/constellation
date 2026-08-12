# Multi-Turn AI Chat Redesign

## Goal

Redesign the AI chat into a proper multi-turn system with server-side conversation persistence, full tool-call/result history preserved across turns, an explicit `task_complete` tool for the AI to signal completion, and a unified chat infrastructure shared by both the regular topology chat and the AI change planner.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Persistence | Server-side JSON files per project (`output/projects/<pid>/conversations/<cid>.json`) | Survives refresh/crash/tab-switch; follows existing `ProjectStore` file pattern; no new deps |
| Chat architecture | Unified shared hook (`useConversationChat`) | Eliminates duplicate SSE handling, state management, tool-chip rendering across GlobalChat + PlannerChat |
| Tool history across turns | Full message history including `tool_calls` + `tool` role messages sent to LLM each turn | LLM can reference prior graph exploration instead of re-deriving |
| Auto-continuation | `task_complete` tool with `status: "complete" | "incomplete"` | AI explicitly signals when done; server loops on `"incomplete"` |
| Runway | 20 tool iterations, 4096 max tokens per iteration | Enough for planner to explore graph + produce full plan without premature truncation |
| Conversation model | One default conversation per project, auto-created on first message | Simple MVP; multiple named conversations as future enhancement |
| Backward compat | Old `/api/projects/{pid}/ai/chat/stream` preserved; frontend switches to conversation-scoped endpoints immediately | No breakage for any external consumers |

---

## 1. Conversation Model & Storage

### 1.1 Data Model (`engine/conversation_store.py` — NEW)

```python
@dataclass
class Conversation:
    id: str                          # uuid
    project_id: str                  # owning project
    title: str                       # auto-generated from first user msg (first ~60 chars)
    messages: list[dict]             # full OpenAI-format message array
    created_at: str                  # ISO 8601
    updated_at: str                  # ISO 8601
```

**Message format** (same as OpenAI's API, stored verbatim):

```json
{"role": "user",                            "content": "How does order processing work?"}
{"role": "assistant", "content": null,      "tool_calls": [{"id":"call_1","type":"function","function":{"name":"search_code","arguments":"{\"query\":\"order\"}"}}]}
{"role": "tool",      "tool_call_id":"call_1","name":"search_code",  "content":"{\"results\":[...]}"}
{"role": "assistant",                       "content": "Order processing starts with..."}
```

### 1.2 Storage Path

```
output/projects/<pid>/conversations/
    <conv_id>.json
```

### 1.3 ConversationStore API

| Method | Description |
|---|---|
| `create(project_id: str) -> Conversation` | New conversation with empty messages and UUID |
| `get(project_id: str, conv_id: str) -> Conversation \| None` | Load from JSON file |
| `list(project_id: str) -> list[Conversation]` | List all conversations for a project |
| `save(project_id: str, conv: Conversation) -> None` | Write to JSON file (atomic: write temp → rename) |
| `delete(project_id: str, conv_id: str) -> None` | Remove conversation file |
| `get_or_create_default(project_id: str) -> Conversation` | Return the single default conversation; create if absent |

**Design notes:**
- `save()` uses temp-file + rename for atomic writes
- `list()` loads only metadata (id, title, updated_at) from index, not full messages — but for MVP with one conversation, load everything
- `ConversationStore` is instantiated at module level in `server.py` (like `ProjectStore`)

---

## 2. Extended Message Model (server.py)

### 2.1 New Pydantic Models

```python
class ToolCallFunction(BaseModel):
    name: str = ""
    arguments: str = ""  # JSON-encoded string

class ToolCall(BaseModel):
    id: str = ""
    type: str = "function"
    function: ToolCallFunction = Field(default_factory=ToolCallFunction)

class ChatMessage(BaseModel):
    role: str = "user"           # user | assistant | system | tool
    content: str = ""
    tool_calls: list[ToolCall] | None = None   # NEW — for assistant messages
    tool_call_id: str | None = None            # NEW — for tool result messages
    name: str | None = None                    # NEW — tool name for tool messages
```

The `ChatRequest` gains:
```python
conversation_id: str = ""  # If empty, uses default conversation
```

Backward compatibility: old frontend code that omits `tool_calls`/`tool_call_id`/`name` still works — those fields default to `None` and are ignored.

---

## 3. New `task_complete` Tool (graph_tools.py)

### 3.1 Tool Definition

```json
{
    "name": "task_complete",
    "description": "Signal completion status of the current task. Call this after you've finished all requested work, or when you need to report interim progress and continue with remaining steps.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["complete", "incomplete"],
                "description": "'complete' when all work is done; 'incomplete' when there are remaining steps to execute"
            },
            "summary": {
                "type": "string",
                "description": "Brief summary of what was accomplished"
            },
            "next_steps": {
                "type": "string",
                "description": "If incomplete, what steps remain. Keep this concise and actionable."
            }
        },
        "required": ["status"]
    }
}
```

### 3.2 Implementation

```python
def task_complete(status: str = "complete", summary: str = "", next_steps: str = ""):
    """Passthrough — the server's tool loop inspects the status field to decide whether to continue."""
    return {
        "type": "task_complete",
        "status": status,
        "summary": summary,
        "next_steps": next_steps,
    }
```

Registered in `TOOL_DEFINITIONS`, `execute_tool` dispatch, and `get_tool_definitions()`.

### 3.3 Server Behavior on `task_complete`

In `_stream_llm_events_v2`:

```
if tool_name == "task_complete":
    if result["status"] == "complete":
        yield tool_result event with summary
        yield done
        save conversation → return
    elif result["status"] == "incomplete":
        yield tool_result event
        append system message: "Continue with: <next_steps>. Do not call task_complete again until you have completed these steps or need to checkpoint."
        continue loop
        # Note: task_complete "incomplete" calls do NOT count toward the 20-iteration limit
        # (or: they count, but we have enough headroom that this is fine)
```

---

## 4. Server-Side Changes: `_stream_llm_events_v2`

### 4.1 Parameters Change

| Parameter | Old | New |
|---|---|---|
| `max_tokens` | 1200 | 4096 |
| Tool iterations | 5 | 20 |
| Tool result truncation (SSE) | 900 chars | 900 chars for display, **full result** for `render_diagram` (panel state + large diagrams) |

### 4.2 Algorithm

```
_stream_llm_events_v2(system_prompt, full_messages, model, tools, graph, conversation):
    
    for iteration in range(20):
        response = LLM(full_messages + tools)
        
        if response.error:
            yield error → return
        
        if response has tool_calls:
            # Execute all tool calls
            tool_calls = parse_tool_calls(response)
            
            # Check for task_complete before executing
            has_task_complete = any(tc.function.name == "task_complete" for tc in tool_calls)
            
            # Execute all tools
            for tc in tool_calls:
                result = execute_tool(graph, tc.function.name, tc.function.arguments)
                
                if tc.function.name == "task_complete":
                    yield {"type": "task_complete", "status": result["status"],
                           "summary": result.get("summary", ""),
                           "next_steps": result.get("next_steps", "")}
                elif tc.function.name == "render_diagram":
                    # Planner-only, stateful tool: full (un-truncated) result +
                    # current panel list so the frontend can mirror deterministically.
                    yield {"type": "tool_result", "name": tc.function.name,
                           "result": json.dumps(result, default=str),  # FULL
                           "diagrams": result.get("diagrams", [])}
                else:
                    yield {"type": "tool_result", "name": tc.function.name,
                           "result": _truncate_json(result)}
                
                full_messages.append({"role": "tool", "tool_call_id": tc.id,
                                      "name": tc.function.name,
                                      "content": json.dumps(result, default=str)})
            
            # Append assistant message with tool_calls
            full_messages.append({"role": "assistant", "content": response.text or None,
                                  "tool_calls": tool_calls})
            
            # Handle task_complete continuation
            if has_task_complete:
                tc_result = ...  # result from task_complete execution
                if tc_result["status"] == "complete":
                    yield {"type": "done"}
                    return
                elif tc_result["status"] == "incomplete":
                    next_steps = tc_result.get("next_steps", "the remaining steps")
                    full_messages.append({"role": "system",
                        "content": f"Continue working. Remaining steps: {next_steps}"})
                    continue  # loop again
            
            continue  # loop to let LLM process tool results
        
        # Text-only response — LLM chose to stop
        yield {"type": "done"}
        return
    
    # Exhausted 20 iterations
    yield {"type": "done"}
```

### 4.3 Conversation Auto-Save

After `_stream_llm_events_v2` completes (normal or exhausted), the calling endpoint saves the `full_messages` back to the conversation JSON.

```
async def conversation_chat_stream(pid, cid, req):
    conv = store.get(pid, cid)
    if not conv: return 404
    
    # Append user message to conversation
    conv.messages.append({"role": "user", "content": req.content})
    
    # Build system prompt from current view context
    system_prompt = _build_chat_prompt(req, graph)
    full_messages = [{"role": "system", "content": system_prompt}] + conv.messages
    
    # Stream response
    async def event_stream():
        for ev in _stream_llm_events_v2(..., full_messages, ...):
            yield format_sse(ev)
        
        # Save conversation after stream ends
        conv.messages = full_messages[1:]  # strip system prompt
        conv.updated_at = _now()
        store.save(pid, conv)
    
    return StreamingResponse(event_stream(), ...)
```

---

## 5. API Endpoints

### 5.1 New Conversation Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/projects/{pid}/conversations` | Create new conversation. Body: `{"title": "optional"}`. Returns `{id, title, created_at}` |
| `GET` | `/api/projects/{pid}/conversations` | List conversations. Returns `[{id, title, updated_at, message_count}]` |
| `GET` | `/api/projects/{pid}/conversations/{cid}` | Get full conversation with messages |
| `DELETE` | `/api/projects/{pid}/conversations/{cid}` | Delete conversation |
| `POST` | `/api/projects/{pid}/conversations/{cid}/chat/stream` | Send message + stream response. Body: `{content, model, entry_point_id, node, repo, flow_context, planner}` |
| `POST` | `/api/projects/{pid}/conversations/{cid}/chat` | Non-streaming variant |

### 5.2 Request Body for Chat Endpoint

```jsonc
{
    "content": "User's message text",
    "model": "deepseek-v4-flash-free",        // optional, defaults to env
    "entry_point_id": "...",                    // optional, for focused node context
    "node": {},                                 // optional, selected call-tree node
    "repo": "",                                 // optional, solar/flow view
    "flow_context": {},                         // optional, flow metadata
    "planner": false                            // optional, use planner system prompt
}
```

Note: `entry_point_id`, `node`, `repo`, `flow_context`, `planner` are the same context fields from the old `ChatRequest`. They determine system prompt, not what the conversation carries.

### 5.3 Old Endpoints (Preserved)

- `POST /api/projects/{pid}/ai/chat` — unchanged, not deprecated
- `POST /api/projects/{pid}/ai/chat/stream` — unchanged, not deprecated

These continue to accept `messages: [{role, content}]` and work statelessly. Frontend switches to conversation-scoped endpoints.

---

## 6. Frontend: Unified Chat Hook

### 6.1 New File: `web/src/useConversationChat.js`

```js
export function useConversationChat({ pid, graph, planner = false }) {
    // State
    const [messages, setMessages] = useState([]);
    const [loading, setLoading] = useState(false);
    const [model, setModel] = useState("");
    const [models, setModels] = useState([]);
    const [error, setError] = useState("");
    const [conversationId, setConversationId] = useState(null);
    const scrollRef = useRef(null);
    const inputRef = useRef(null);
    
    // Load/create default conversation on mount + pid change
    useEffect(() => {
        loadDefaultConversation(pid);
    }, [pid]);
    
    // Load models on mount
    useEffect(() => {
        fetchJSON("/api/ai/models").then(m => { setModels(m.models); setModel(m.models[0]); });
    }, []);
    
    // Auto-scroll
    useEffect(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }, [messages, loading]);
    
    // ── Core: send a message ──
    async function send(text) {
        // ... same SSE pattern as current send() but:
        // 1. POSTs to /api/projects/{pid}/conversations/{cid}/chat/stream
        // 2. Body: { content: text, model, planner, ...ctxPayload }
        // 3. Auto-creates conversation on first message if cid is null
        // 4. SSE reader patches message array with token/tool_start/tool_result/done
        // 5. Messages array now includes tool_calls + tool_call_id fields
    }
    
    // ── New conversation ──
    async function newConversation() {
        const res = await fetchJSON(`/api/projects/${pid}/conversations`, { method: "POST" });
        setConversationId(res.id);
        setMessages([]);
    }
    
    // ── Load conversation ──
    async function loadConversation(cid) { ... }
    
    return {
        messages, loading, model, models, error,
        send, newConversation,
        setModel, setError,
        scrollRef, inputRef,
    };
}
```

### 6.2 Message Format Change

Old frontend message shape:
```js
{ role: "assistant", content: "...", tools: [{name, args, status, result}], streaming: boolean }
```

New frontend message shape (aligned with OpenAI format):
```js
{ role: "assistant", content: "...", tool_calls: [{id, type:"function", function:{name, arguments}}], streaming: boolean }
{ role: "tool", tool_call_id: "call_1", name: "search_code", content: "{...}" }
```

The `tools` field on UI message objects becomes derived — computed from `tool_calls` + subsequent `tool` messages for rendering tool chips. This is handled in the render function, not the state.

Tool chips render from: find assistant messages with `tool_calls`, match each `tool_call` to its `tool` result message by `tool_call_id`.

### 6.3 Component Changes

**`GlobalChat`** (in `app.jsx`):
- Replace `useState` messages/loading/model state with `useConversationChat({ pid, graph, planner: false })`
- Remove duplicate SSE reader code
- Keep floating panel layout
- Pass `ctx.payload` to the hook's `send()`
- **No longer unmounts on tab switch** — move it outside the per-mode conditional, keyed by `pid` so it remounts on project change

**`PlannerChat`** (in `changePlanner.jsx`):
- Replace `useState` messages/loading/model state with `useConversationChat({ pid, graph, planner: true })`
- Remove duplicate SSE reader code
- Keep inline left-pane layout
- Intercept `render_diagram` tool results (drives the right-side preview panel):
  - In the SSE handler (`onToolResult`), check `ev.name === "render_diagram"`
  - `ev.diagrams` carries the **full** current panel list (un-truncated) — the frontend mirrors from it
  - Lift it to the panel via `onDiagrams(ev.diagrams)`
- **Stays mounted** across mode switches — keyed by `pid` so it remounts on project change

**`App`** (in `app.jsx`):
- Move `GlobalChat` outside the per-`mode` render switch, like:
  ```jsx
  {mode !== "planner" && <GlobalChat key={activeId} graph={...} pid={activeId} ... />}
  {mode === "planner" && <PlannerChat key={activeId} graph={...} pid={activeId} ... />}
  ```
  Wait — actually both should be mounted but only the active one visible. Or we mount/unmount but the conversation state is server-side, so state survives.

**Decision:** Mount on mode switch, but since conversation loads from server, state is preserved. `key={activeId}` ensures remount on project switch (which should clear local state and reload). The conversation from the server carries the full message history.

---

## 7. Truncation Bug Fix

### Problem

`_stream_llm_events` truncates ALL tool results at 900 chars for SSE display:
```python
yield {"type": "tool_result", "name": tool_name, "result": _truncate_json(tool_result)}
```

For `render_diagram`, the result JSON (panel state + diagram bodies) is often > 900 chars. A truncated `ev.result` would corrupt the frontend mirror, so it must not be truncated.

### Fix

Special-case `render_diagram` to send the full, un-truncated result, plus the current panel list for the frontend to mirror:
```python
if tool_name == "render_diagram":
    yield {
        "type": "tool_result",
        "name": tool_name,
        "result": json.dumps(tool_result, default=str),  # FULL
        "diagrams": tool_result.get("diagrams", []),  # current panel state
    }
else:
    yield {"type": "tool_result", "name": tool_name, "result": _truncate_json(tool_result)}
```

Frontend `PlannerChat` mirrors the panel from `ev.diagrams` (via the `onToolResult` hook callback) instead of parsing `ev.result`.

---

## 8. Migration Path

No user-visible data migration needed — there is no persisted chat data to migrate. The migration is purely in-memory:

1. Deploy new backend with conversation endpoints + old endpoints
2. Deploy new frontend that uses conversation-scoped endpoints
3. On first chat open, frontend auto-creates a default conversation and loads from scratch
4. Any external consumer still hitting old `/api/ai/chat/stream` continues to work statelessly

---

## 9. Implementation Order

### Phase 1: Backend Foundation

| Step | File | Description |
|---|---|---|
| 1 | `engine/conversation_store.py` | **NEW.** `ConversationStore` class with create/get/list/save/delete + JSON file I/O |
| 2 | `server.py` | Extend `ChatMessage` with `tool_calls`, `tool_call_id`, `name` fields |
| 3 | `server.py` | Add `ChatRequest.conversation_id` field |
| 4 | `server.py` | Add `ConversationRequest` model: `{content, model, entry_point_id, node, repo, flow_context, planner}` |
| 5 | `server.py` | Add CRUD endpoints: `POST` create, `GET` list, `GET` by id, `DELETE` |
| 6 | `server.py` | Add `POST /{pid}/conversations/{cid}/chat/stream` — conversation-scoped streaming |
| 7 | `engine/graph_tools.py` | Add `task_complete` tool: definition + execute + dispatch |
| 8 | `server.py` | Rewrite `_stream_llm_events_v2`: 20 iterations, 4096 tokens, task_complete handling, full tool history, diagram untruncated |
| 9 | `server.py` | Conversation auto-save after stream completes |

**Validation**: `curl` a conversation creation + chat message, verify JSON file is written with full tool history.

### Phase 2: Frontend Unified Hook

| Step | File | Description |
|---|---|---|
| 10 | `web/src/useConversationChat.js` | **NEW.** Shared chat hook: conversation CRUD, SSE streaming, message state |
| 11 | `web/src/app.jsx` | Rewrite `GlobalChat` to use the hook; remove duplicate SSE code |
| 12 | `web/src/changePlanner.jsx` | Rewrite `PlannerChat` to use the hook; remove duplicate SSE code |
| 13 | `web/src/changePlanner.jsx` | Fix diagram interception to use `ev.diagram` |

**Validation**: Open topology chat → send message → switch to planner → planner loads its conversation → switch back → topology chat still has history.

### Phase 3: Polish & Bug Fixes

| Step | File | Description |
|---|---|---|
| 14 | `web/src/changePlanner.jsx` | `ImpactDiagram`: activate `onNodeClick` prop (currently wired but never passed) |
| 15 | `web/src/app.jsx` | Add `+ New Chat` button that creates a new conversation (or defer to future) |
| 16 | `server.py` | Ensure old `/api/ai/chat/stream` still functional for backward compat |

---

## 10. Risks & Edge Cases

### 10.1 Token Budget

With 20 iterations × 4096 tokens per response, plus tool results accumulating in `full_messages`, the context window could exceed provider limits (typically 8K–32K for free models). 

**Mitigation**: Tool results are stored as-is in the conversation but could be summarized if messages exceed a threshold (e.g., 32K total chars). For MVP, no truncation — the 4096 token cap per response naturally limits growth.

### 10.2 Infinite Loop on `task_complete(incomplete)`

If the LLM repeatedly calls `task_complete` with `"incomplete"` without making progress, the 20-iteration limit acts as a hard guard. After 20 iterations, the loop exits regardless.

### 10.3 Concurrent Conversation Writes

A user could open two browser tabs and send messages in both. The last `save()` wins. 

**Mitigation**: Each request atomically reads-saves the conversation file. This is acceptable for a single-user local tool (which Constellation is).

### 10.4 Conversation File Growth

Each LLM turn adds a user message + assistant message(s) + tool messages to the JSON file. Over many turns, the file could grow large (several hundred KB).

**Mitigation**: For MVP, no action — JSON files handle tens of MB fine. Future: add conversation pruning (keep last N messages) or message-count cap.

### 10.5 `task_complete` Prompt Integration

The LLM needs to know about the `task_complete` tool and when to call it. The planner system prompt (and the global system prompt) should include instructions:

```
When you have completed all requested work, call `task_complete` with
status: "complete". If you have more work to do but want to report
progress, call it with status: "incomplete" and describe next_steps.
```

This is included in the tool description already; the system prompt should also reinforce it.

### 10.6 Old Client Compatibility

Clients sending `messages: [{role, content}]` to the old endpoint won't include tool calls. The old endpoint should detect if messages contain `tool_calls` and forward them. Since the old endpoint converts pydantic `ChatMessage` → dict, and `ChatMessage` now has optional `tool_calls`/`tool_call_id`/`name`, old clients that don't send these fields will have them as `None`, which is fine.

---

## 11. Open / Deferred

| Item | Status |
|---|---|
| Multiple named conversations per project (sidebar list, rename, delete) | **Deferred.** MVP: one default conversation per project |
| Conversation title auto-generation from first message | **MVP.** First 60 chars of first user message |
| Tool result summarization for large histories | **Deferred.** Monitor file growth first |
| `ImpactDiagram` node click → detail | **Deferred.** Low priority, but `onNodeClick` prop should be wired in this redesign since the component accepts it |
| Remount behavior on tab switch | **Resolved.** Conversations loaded from server, so state survives. Only project switch (`pid` change) triggers re-mount + reload |
