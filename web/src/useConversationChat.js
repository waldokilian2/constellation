import { useState, useEffect, useRef, useCallback } from "react";

const projPath = (pid, rest) => "/api/projects/" + encodeURIComponent(pid) + rest;

async function fetchJSON(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error("HTTP " + res.status + " " + res.statusText);
  return res.json();
}

function safeParseArgs(argsStr) {
  if (typeof argsStr === "object") return argsStr;
  try { return JSON.parse(argsStr || "{}"); } catch { return {}; }
}

/**
 * Convert stored OpenAI-format messages into the frontend's render model.
 *
 * The server stores the full history including `tool`-role result messages
 * and per-turn `system` nudges. For rendering we:
 *   - fold each `tool` result back into its parent assistant message's tool
 *     chip (matched by tool_call_id),
 *   - drop standalone `tool` and `system` messages (never chat bubbles),
 *   - give every message a `tools` array (possibly empty).
 *
 * Result: a clean alternation of user / assistant entries, where an assistant
 * entry may carry tool chips and/or text.
 */
function normalizeMessages(raw) {
  const toolResults = {};
  for (const m of raw) {
    if (m.role === "tool" && m.tool_call_id) {
      toolResults[m.tool_call_id] = m.content || "";
    }
  }
  const out = [];
  for (const m of raw) {
    if (m.role === "tool" || m.role === "system") continue;
    const base = { ...m, streaming: false };
    if (m.role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) {
      base.tools = m.tool_calls.map((tc) => {
        const fn = tc.function || {};
        const id = tc.id || "";
        return {
          name: fn.name || tc.name || "",
          args: safeParseArgs(fn.arguments),
          status: "done",
          result: (id && toolResults[id]) || "",
        };
      });
    } else {
      base.tools = [];
    }
    out.push(base);
  }
  return out;
}

export function useConversationChat({ pid, ctxPayload, planner = false, onToolResult = null }) {
  // The conversation surface: the per-page assistant ("chat") and the AI
  // Change Planner ("planner") keep SEPARATE histories with different system
  // prompts. The kind is derived 1:1 from the planner flag so callers don't
  // need a second knob, and it scopes list/create/default on the server.
  const kind = planner ? "planner" : "chat";
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState("");
  const [models, setModels] = useState([]);
  const [error, setError] = useState("");
  const [conversationId, setConversationId] = useState(null);
  const [convList, setConvList] = useState([]);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  // Snapshot of committed history captured at send time; in-flight segments
  // are appended to this so the live view never mutates prior turns.
  const baseRef = useRef([]);
  // AbortController for the in-flight stream, so Stop can cancel it.
  const abortRef = useRef(null);

  const refreshConvList = useCallback(async () => {
    try {
      const list = await fetchJSON(projPath(pid, "/conversations?kind=" + encodeURIComponent(kind)));
      const convs = (list.conversations || []).filter((c) => (c.kind || "chat") === kind);
      setConvList(convs);
      return convs;
    } catch (e) {
      return [];
    }
  }, [pid, kind]);

  // ── Load/create default conversation on mount + pid/kind change ──
  useEffect(() => {
    let alive = true;
    async function init() {
      try {
        const convs = await refreshConvList();
        let cid = null;
        if (convs.length > 0) {
          cid = convs[0].id;
        } else {
          // Auto-create default conversation for this surface.
          const created = await fetchJSON(projPath(pid, "/conversations"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "Default conversation", kind }),
          });
          cid = created.id;
          await refreshConvList();
        }
        if (alive && cid) {
          const conv = await fetchJSON(projPath(pid, "/conversations/" + cid));
          setConversationId(cid);
          setMessages(normalizeMessages(conv.messages || []));
        }
      } catch (e) {
        // project/graph may not be loaded yet — retried on next pid change
      }
    }
    init();
    return () => { alive = false; };
  }, [pid, kind, refreshConvList]);

  // ── Load models on mount ──
  useEffect(() => {
    let alive = true;
    fetchJSON("/api/ai/models")
      .then((m) => { if (alive) { setModels(m.models || []); setModel(m.models && m.models.length ? m.models[0] : ""); } })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // ── Auto-scroll ──
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  // ── Send a message ──
  // The server streams one `message_start` per LLM iteration. Each starts a
  // fresh assistant segment, so a multi-step turn renders as separate entries
  // (tool chips, then the final answer) instead of one jumbled bubble.
  const send = useCallback(async (text) => {
    const msg = text.trim();
    if (!msg || loading) return;

    const base = messages.map((m) => ({ ...m, streaming: false }));
    baseRef.current = base;
    const userMsg = { role: "user", content: msg };

    // In-flight assistant segments for this send.
    const segs = [];
    let cur = null;

    const newSegment = () => {
      cur = { role: "assistant", content: "", reasoning: "", tools: [], streaming: true };
      segs.push(cur);
      return cur;
    };
    const flush = () => {
      setMessages([
        ...baseRef.current,
        userMsg,
        ...segs.map((s) => ({
          role: s.role,
          content: s.content,
          reasoning: s.reasoning,
          tools: s.tools.map((t) => ({ ...t })),
          streaming: s.streaming,
        })),
      ]);
    };
    const patchCur = (fn) => { fn(cur); flush(); };
    const ensureSeg = () => { if (!cur) newSegment(); return cur; };

    flush();
    setError("");
    setLoading(true);

    try {
      const cid = conversationId;
      const controller = new AbortController();
      abortRef.current = controller;
      const res = await fetch(projPath(pid, "/conversations/" + cid + "/chat/stream"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...(ctxPayload || {}), content: msg, model, planner }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        const body = await res.text().catch(() => "");
        throw new Error(`Stream failed (${res.status}) ${body.slice(0, 200)}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop();
        for (const evRaw of events) {
          const line = evRaw.trim();
          if (!line.startsWith("data:")) continue;
          const dataStr = line.slice(5).trim();
          if (!dataStr || dataStr === "[DONE]") continue;
          let ev;
          try { ev = JSON.parse(dataStr); } catch { continue; }

          if (ev.type === "message_start") {
            newSegment();
            flush();
          } else if (ev.type === "token" && ev.text) {
            patchCur((s) => { s.content += ev.text; });
          } else if (ev.type === "reasoning" && ev.text) {
            patchCur((s) => { s.reasoning = (s.reasoning || "") + ev.text; });
          } else if (ev.type === "tool_start") {
            ensureSeg();
            patchCur((s) => { s.tools.push({ name: ev.name, args: ev.args || {}, status: "running", result: "" }); });
          } else if (ev.type === "tool_result") {
            patchCur((s) => {
              s.tools = s.tools.map((t) =>
                t.name === ev.name && t.status === "running"
                  ? { ...t, status: "done", result: ev.result || "" }
                  : t
              );
            });
            if (onToolResult) onToolResult(ev.name, ev);
          } else if (ev.type === "task_complete") {
            // The AI signalled completion. If it streamed no prose this turn,
            // surface its `message` as the assistant's final bubble — otherwise
            // the user-facing reply would be lost. In planner mode this same
            // message doubles as the plan-readiness statement.
            if (ev.message) {
              ensureSeg();
              patchCur((s) => { if (!s.content) s.content = ev.message; });
            }
          } else if (ev.type === "stopped") {
            // User-initiated Stop — finalize gracefully without an error.
            segs.forEach((s) => { s.streaming = false; });
            flush();
          } else if (ev.type === "error") {
            setError(ev.message || "Stream error");
          } else if (ev.type === "done") {
            segs.forEach((s) => { s.streaming = false; });
            flush();
          }
        }
      }
      segs.forEach((s) => { s.streaming = false; });
      flush();
    } catch (e) {
      if (e && e.name === "AbortError") {
        // user hit Stop — keep whatever streamed, no error toast
      } else {
        setError(e.message);
      }
      segs.forEach((s) => { s.streaming = false; });
      flush();
    } finally {
      abortRef.current = null;
      setLoading(false);
    }
  }, [messages, loading, model, pid, conversationId, ctxPayload, planner, onToolResult]);

  // ── Stop the in-flight stream (user clicked Stop) ──
  const stop = useCallback(() => {
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch {}
      abortRef.current = null;
    }
  }, []);

  // ── New conversation ──
  const newConversation = useCallback(async () => {
    try {
      const res = await fetchJSON(projPath(pid, "/conversations"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "", kind }),
      });
      setConversationId(res.id);
      setMessages([]);
      setError("");
      await refreshConvList();
    } catch (e) {
      // ignore
    }
  }, [pid, kind, refreshConvList]);

  // ── Load (switch to) an existing conversation ──
  const loadConversation = useCallback(async (cid) => {
    if (!cid) return;
    try {
      const conv = await fetchJSON(projPath(pid, "/conversations/" + cid));
      setConversationId(cid);
      setMessages(normalizeMessages(conv.messages || []));
      setError("");
    } catch (e) {
      // conversation missing — refresh list
      await refreshConvList();
    }
  }, [pid, refreshConvList]);

  // ── Delete a conversation ──
  const deleteConversation = useCallback(async (cid) => {
    if (!cid) return false;
    try {
      await fetch(projPath(pid, "/conversations/" + cid), { method: "DELETE" });
      const remaining = await refreshConvList();
      // If we deleted the active one, fall back to the next (or a fresh one).
      if (cid === conversationId) {
        if (remaining.length > 0) {
          await loadConversation(remaining[0].id);
        } else {
          await newConversation();
        }
      }
      return true;
    } catch (e) {
      return false;
    }
  }, [pid, conversationId, refreshConvList, loadConversation, newConversation]);

  return {
    messages, loading, model, models, error,
    send, stop, newConversation, loadConversation, deleteConversation,
    setModel, setError,
    scrollRef, inputRef,
    conversationId, convList, refreshConvList,
  };
}
