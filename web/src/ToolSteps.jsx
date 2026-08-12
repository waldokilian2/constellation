/* ============================================================
   Shared tool-call step chips for chat messages (GlobalChat +
   PlannerChat). Renders each tool call as an expandable chip:
   status badge + tool name + a compact human-friendly args
   preview, with the full JSON result tucked inside a <details>
   body. Control tools like `task_complete` are hidden.
   ============================================================ */

import React from "react";

/* Compact, human-friendly args preview for the chip summary line.
 * The full args + result stay one click away in the <details> body. */
function fmtToolArgs(args) {
  if (!args || typeof args !== "object") return "";
  const a = { ...args };
  // These fields dominate the payload and read as noise in a chip.
  for (const k of ["code", "content", "text", "source", "prompt"]) delete a[k];
  const entries = Object.entries(a);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => {
      let s = typeof v === "string" ? v : JSON.stringify(v);
      if (s.length > 42) s = s.slice(0, 42) + "…";
      return `${k}: ${s}`;
    })
    .join(" · ");
}

/* SVG status icon — stroke inherits currentColor so CSS tints it. */
function StatusIcon({ status }) {
  if (status === "running") {
    return (
      <svg viewBox="0 0 24 24" className="tool-spin-svg" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden="true">
        <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      </svg>
    );
  }
  if (status === "done") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden="true">
      <path d="M12 8v5" />
      <circle cx="12" cy="16.5" r="0.5" fill="currentColor" />
    </svg>
  );
}

/**
 * Renders a list of tool calls as expandable status chips.
 * `tools` — [{ name, args, status, result }] in UI shape.
 * Chips open automatically while a tool is running; afterwards the
 * user can toggle them freely (uncontrolled via defaultOpen).
 */
export default function ToolSteps({ tools }) {
  const visible = (tools || []).filter((t) => t.name !== "task_complete");
  if (!visible.length) return null;

  return (
    <div className="chat-tools chat-tools-step">
      {visible.map((t, ti) => {
        const status = t.status || "idle";
        const args = fmtToolArgs(t.args);
        return (
          <details key={ti} className={"chat-tool " + status} defaultOpen={status === "running"}>
            <summary>
              <span className={"tool-status " + status}><StatusIcon status={status} /></span>
              <span className="tool-name">{t.name}</span>
              {args && <span className="tool-args">{args}</span>}
              {status === "running" && <span className="tool-running">working…</span>}
              <span className="tool-chevron" aria-hidden="true">▾</span>
            </summary>
            <pre className="tool-result">{t.result}</pre>
          </details>
        );
      })}
    </div>
  );
}
