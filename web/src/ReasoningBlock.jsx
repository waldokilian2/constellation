import React from "react";

/* ============================================================
   Collapsible chain-of-thought block shown above an assistant
   reply. Rendered only when the provider streams reasoning
   tokens (DeepSeek `reasoning_content` / Qwen `reasoning`) and
   the server captured them — otherwise this returns null.
   While streaming it stays open ("thinking…"); afterwards the
   user can toggle it freely (uncontrolled via defaultOpen).
   ============================================================ */

export default function ReasoningBlock({ text, live = false }) {
  if (!text || !text.trim()) return null;
  return (
    <details className="reasoning-block" defaultOpen={live}>
      <summary>
        <span className="reasoning-ico" aria-hidden="true">🧠</span>
        <span className="reasoning-label">Reasoning</span>
        {live && <span className="reasoning-live">thinking…</span>}
      </summary>
      <div className="reasoning-body">{text}</div>
    </details>
  );
}
