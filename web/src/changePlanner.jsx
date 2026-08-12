/* ============================================================
   CONSTELLATION — AI Change Planner
   Split pane: markdown-native chat (left) + diagram canvas
   (right). The right-side panel is driven by the AI's
   `render_diagram` tool (add / replace / remove / get / clear),
   whose state is authoritative on the server (persisted with the
   conversation). The chat still renders markdown inline; only the
   panel is tool-managed. Reuses .chat-msg / .chat-tool etc. from
   GlobalChat for message rendering.
   ============================================================ */

import React, { useState, useEffect, useCallback, useRef } from "react";
import { useConversationChat } from "./useConversationChat.js";
import ConversationMenu from "./ConversationMenu.jsx";
import MarkdownContent, { sanitizeHTML } from "./Markdown.jsx";
import MermaidDiagram from "./mermaid.jsx";
import ReasoningBlock from "./ReasoningBlock.jsx";
import ToolSteps from "./ToolSteps.jsx";

/* ── PlannerChat ─────────────────────────────────────────────── */
function PlannerChat({ graph, pid, onDiagrams, onConversation }) {
  const [input, setInput] = useState("");

  const {
    messages, loading, model, models, error,
    send, newConversation, loadConversation, deleteConversation,
    setModel, setError,
    scrollRef, inputRef,
    conversationId, convList, refreshConvList,
  } = useConversationChat({
    pid,
    ctxPayload: { planner: true, entry_point_id: "", node: {} },
    planner: true,
    // Mirror the server-authoritative panel state whenever the AI
    // calls render_diagram. ev.diagrams is the full current list.
    onToolResult: (name, ev) => {
      if (name === "render_diagram" && Array.isArray(ev.diagrams)) {
        if (onDiagrams) onDiagrams(ev.diagrams);
      }
    },
  });

  // Lift the active conversation id so the panel can (re)load its
  // diagrams on conversation switch / new plan.
  useEffect(() => {
    if (onConversation) onConversation(conversationId);
  }, [conversationId, onConversation]);

  const [showHistory, setShowHistory] = useState(false);
  const openHistory = () => { refreshConvList(); setShowHistory(true); };

  useEffect(() => {
    if (messages.length === 0 && inputRef.current) inputRef.current.focus();
  }, [messages.length]);

  const repos = (graph && graph.repos) || [];
  const channelCount = (graph && graph.cross_repo_links) ? graph.cross_repo_links.length : 0;
  const epCount = (graph && graph.entry_points) ? graph.entry_points.length : 0;

  const sendMsg = (text) => {
    if (!text.trim() || loading) return;
    send(text.trim());
    setInput("");
  };

  const newPlan = () => {
    newConversation();
  };

  return (
    <div className="planner-chat">
      {/* Header */}
      <div className="planner-chat-header">
        <div className="planner-chat-title">
          <span className="planner-chat-spark">✦</span>
          <span>AI Change Planner</span>
          <span className="planner-chat-sub">· {repos.length} repos · {channelCount} channels</span>
        </div>
        <div className="planner-chat-controls">
          <button className="planner-chat-history" onClick={openHistory} title="Past conversations" disabled={loading}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l3 2" />
            </svg>
          </button>
          {models.length > 0 && (
            <select className="planner-chat-model" value={model}
              onChange={(e) => setModel(e.target.value)} disabled={loading}>
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
          <button className="planner-chat-new" onClick={newPlan} title="New plan">+ New Plan</button>
        </div>
      </div>

      {showHistory && (
        <ConversationMenu
          conversations={convList}
          activeId={conversationId}
          onSelect={(cid) => { loadConversation(cid); }}
          onDelete={(cid) => deleteConversation(cid)}
          onClose={() => setShowHistory(false)}
        />
      )}

      {/* Context bar */}
      <div className="planner-ctx">
        <span className="planner-ctx-dot" />
        <span className="planner-ctx-label">Context</span>
        <span className="planner-ctx-value">Architecture overview · {repos.length} repos · {channelCount} channels</span>
      </div>

      {/* Messages */}
      <div className="planner-chat-body" ref={scrollRef}>
        {messages.length === 0 && !loading && (
          <div className="chat-welcome">
            <div className="chat-welcome-icon">✦</div>
            <p className="planner-welcome-title">Plan architecture changes before you code</p>
            <p className="muted small">Describe a change and the planner maps its blast radius across services — then renders diagrams for the affected flows on the right.</p>
            <div className="planner-welcome-stats">
              <span className="planner-welcome-stat"><b>{repos.length}</b> repos</span>
              <span className="planner-welcome-stat"><b>{epCount}</b> entry points</span>
              <span className="planner-welcome-stat"><b>{channelCount}</b> channels</span>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <React.Fragment key={i}>
            {/* Text bubble — user always; assistant when it has real text or is streaming w/o tools yet */}
            {(msg.role === "user" || (msg.content && msg.content.trim()) || msg.reasoning || (msg.streaming && (!msg.tools || !msg.tools.length))) && (
              <div className={`chat-msg ${msg.role}${msg.streaming ? " streaming" : ""}`}>
                <div className="chat-msg-role">
                  {msg.role === "user" ? "You" : "Planner"}{msg.streaming ? " \uD83D\uDD2E" : ""}
                </div>
                <div className="chat-msg-text">
                  <ReasoningBlock text={msg.reasoning} live={msg.streaming} />
                  <MarkdownContent src={msg.content || ""} live={msg.streaming} />
                </div>
                {msg.streaming && !msg.content && !msg.reasoning && <div className="ai-loading">Planning<span className="dots" /></div>}
                {msg.streaming && msg.content && <span className="stream-caret" />}
              </div>
            )}
            {/* Tool chips as a standalone step (control tools like task_complete are hidden) */}
            {msg.tools && msg.tools.some((t) => t.name !== "task_complete") && (
              <ToolSteps tools={msg.tools} />
            )}
          </React.Fragment>
        ))}

        {loading && !messages.some((m) => m.streaming) && (
          <div className="chat-msg assistant">
            <div className="chat-msg-role">Planner</div>
            <div className="ai-loading">Planning<span className="dots" /></div>
          </div>
        )}

        {error && <div className="ai-error-msg">{error}</div>}
      </div>

      {/* Input */}
      <div className="planner-chat-input">
        <textarea
          ref={inputRef}
          placeholder="Describe a change to plan…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter (or Ctrl/Cmd+Enter) inserts a newline.
            if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
              e.preventDefault();
              sendMsg(input);
            }
          }}
          disabled={loading}
          rows={3}
        />
        <button
          className={"planner-chat-send" + (input.trim() && !loading ? " active" : "")}
          onClick={() => sendMsg(input)}
          disabled={!input.trim() || loading}
          aria-label="Send message"
          title="Send"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
            <path d="M3.4 20.4l17.45-7.48c.81-.35.81-1.49 0-1.84L3.4 3.6c-.66-.29-1.39.2-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z" />
          </svg>
        </button>
      </div>
    </div>
  );
}

/* ── Diagram export helpers ──────────────────────────────────── */
// Rasterize a rendered mermaid <svg> into a PNG download. The SVG is
// cloned with an explicit pixel size + xmlns (so re-serialized
// foreignObject labels keep the XHTML namespace), loaded as an image,
// drawn onto a dark canvas at 2x for crispness, and exported.
function downloadSvgAsPng(svg, filename) {
  const clone = svg.cloneNode(true);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
  clone.querySelectorAll("foreignObject > *").forEach((el) => {
    if (!el.getAttribute("xmlns")) el.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  });

  const vb = svg.viewBox ? svg.viewBox.baseVal : null;
  const width = (vb && vb.width) || svg.getBoundingClientRect().width || 320;
  const height = (vb && vb.height) || svg.getBoundingClientRect().height || 180;
  const scale = 2;
  clone.setAttribute("width", width * scale);
  clone.setAttribute("height", height * scale);

  const svgString = new XMLSerializer().serializeToString(clone);
  const url = URL.createObjectURL(new Blob([svgString], { type: "image/svg+xml;charset=utf-8" }));

  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#0a0e1a"; // match app --bg so exports aren't transparent
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  img.onerror = () => URL.revokeObjectURL(url);
  img.src = url;
}

// Trigger a plain text/html file download from a string.
function downloadTextFile(text, filename, type = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ── PlanPreviewPane (right side, tool/state-driven) ─────────── */
function PlanPreviewPane({ pid, cid, diagrams, setDiagrams }) {
  // (Re)load the authoritative panel state when the conversation changes.
  useEffect(() => {
    if (!pid || !cid) { setDiagrams([]); return; }
    let alive = true;
    fetch(`/api/projects/${encodeURIComponent(pid)}/conversations/${encodeURIComponent(cid)}/diagrams`)
      .then((r) => (r.ok ? r.json() : { diagrams: [] }))
      .then((d) => { if (alive && Array.isArray(d.diagrams)) setDiagrams(d.diagrams); })
      .catch(() => {});
    return () => { alive = false; };
  }, [pid, cid, setDiagrams]);

  const removeDiagram = useCallback(async (id) => {
    if (!pid || !cid || !id) return;
    setDiagrams((cur) => cur.filter((d) => d.id !== id));
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(pid)}/conversations/${encodeURIComponent(cid)}/diagrams/${encodeURIComponent(id)}`,
        { method: "DELETE" }
      );
      if (res.ok) {
        const d = await res.json();
        if (Array.isArray(d.diagrams)) setDiagrams(d.diagrams);
      }
    } catch { /* optimistic update stands */ }
  }, [pid, cid, setDiagrams]);

  const clearAll = useCallback(async () => {
    if (!pid || !cid) return;
    setDiagrams([]);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(pid)}/conversations/${encodeURIComponent(cid)}/diagrams`,
        { method: "DELETE" }
      );
      if (res.ok) {
        const d = await res.json();
        if (Array.isArray(d.diagrams)) setDiagrams(d.diagrams);
      }
    } catch { /* optimistic update stands */ }
  }, [pid, cid, setDiagrams]);

  const count = diagrams.length;

  // Per-card body refs so the download action can grab the rendered
  // <svg> out of the DOM for rasterization.
  const bodyRefs = useRef({});

  const downloadDiagram = useCallback((d) => {
    const isHtml = (d.kind || "mermaid") === "html";
    const base = (d.header || "diagram").replace(/[^\w.-]+/g, "_").slice(0, 60) || "diagram";
    if (isHtml) {
      downloadTextFile(d.code || "", `${base}.html`, "text/html;charset=utf-8");
      return;
    }
    const svg = bodyRefs.current[d.id] && bodyRefs.current[d.id].querySelector(".mermaid-render svg");
    if (svg) downloadSvgAsPng(svg, `${base}.png`);
  }, []);

  return (
    <div className="planner-right">
      <div className="preview-pane-header">
        <span className="preview-pane-title">Plan Preview</span>
        {count > 0 && (
          <div className="preview-pane-actions">
            <span className="preview-pane-sub">{count} diagram{count > 1 ? "s" : ""}</span>
            <button className="preview-pane-clear" onClick={clearAll} title="Clear all diagrams">Clear</button>
          </div>
        )}
      </div>
      <div className="preview-pane-body">
        {count > 0 && diagrams.map((d) => (
          <div className="diagram-card" key={d.id}>
            <div className="diagram-card-header">
              <span className="diagram-card-title" title={d.header}>{d.header || "Diagram"}</span>
              <span className={"diagram-card-kind kind-" + (d.kind || "mermaid")}>{d.kind || "mermaid"}</span>
              <button className="diagram-card-download" onClick={() => downloadDiagram(d)}
                title={"Download " + ((d.kind || "mermaid") === "html" ? "HTML" : "PNG")}
                aria-label="Download diagram">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              </button>
              <button className="diagram-card-remove" onClick={() => removeDiagram(d.id)} title="Remove diagram" aria-label="Remove diagram">✕</button>
            </div>
            <div className="diagram-card-body" ref={(el) => { bodyRefs.current[d.id] = el; }}>
              {(d.kind || "mermaid") === "html"
                ? <div className="html-preview" dangerouslySetInnerHTML={{ __html: sanitizeHTML(d.code || "") }} />
                : <MermaidDiagram code={d.code || ""} />}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── ChangePlannerView (default export) ──────────────────────── */
export default function ChangePlannerView({ graph, pid }) {
  const [diagrams, setDiagrams] = useState([]);
  const [cid, setCid] = useState("");

  // Reset the panel when switching projects (the conversation id will
  // arrive shortly after via onConversation and trigger a fetch).
  useEffect(() => { setDiagrams([]); }, [pid]);

  return (
    <div className="planner">
      <div className="planner-left">
        <PlannerChat
          graph={graph}
          pid={pid}
          onDiagrams={setDiagrams}
          onConversation={setCid}
        />
      </div>
      <PlanPreviewPane pid={pid} cid={cid} diagrams={diagrams} setDiagrams={setDiagrams} />
    </div>
  );
}
