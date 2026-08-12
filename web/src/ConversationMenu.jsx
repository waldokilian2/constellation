import React, { useEffect, useRef } from "react";

/* ── relative time formatter (self-contained; app.jsx's fmtRelative isn't exported) ── */
function fmtRelative(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (isNaN(diff)) return "";
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
  return d.toLocaleDateString();
}

/**
 * Conversation switcher dropdown. Lists past conversations (title + relative
 * time + message count), highlights the active one, and lets the user switch
 * to or delete any of them.
 *
 * Props:
 *   conversations — [{ id, title, updated_at, message_count }]
 *   activeId      — currently loaded conversation id
 *   onSelect(cid) — switch to a conversation
 *   onDelete(cid) — delete a conversation (returns true on success)
 *   onClose()     — close the dropdown
 */
export default function ConversationMenu({ conversations, activeId, onSelect, onDelete, onClose }) {
  const ref = useRef(null);

  // Close on outside click / Escape.
  useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const list = conversations || [];

  async function handleDelete(e, cid) {
    e.stopPropagation();
    if (onDelete) {
      const ok = await onDelete(cid);
      // If the menu is now empty / closed by parent, nothing more to do.
      void ok;
    }
  }

  return (
    <div className="conv-menu glass" ref={ref} role="menu" aria-label="Past conversations">
      <div className="conv-menu-head">
        <span className="conv-menu-title">Conversations</span>
        <span className="conv-menu-count">{list.length}</span>
      </div>
      <div className="conv-menu-list">
        {list.length === 0 && (
          <div className="conv-menu-empty muted small">No conversations yet.</div>
        )}
        {list.map((c) => {
          const active = c.id === activeId;
          const title = c.title || "New conversation";
          return (
            <div
              key={c.id}
              className={"conv-menu-item" + (active ? " active" : "")}
              onClick={() => { onSelect && onSelect(c.id); onClose(); }}
              role="menuitem"
            >
              <div className="conv-menu-item-main">
                <span className="conv-menu-item-title">{title}</span>
                <span className="conv-menu-item-meta">
                  {typeof c.message_count === "number" ? c.message_count + " msg" : ""}
                  {" · "}
                  {fmtRelative(c.updated_at)}
                </span>
              </div>
              <button
                className="conv-menu-item-del"
                title="Delete conversation"
                aria-label="Delete conversation"
                onClick={(e) => handleDelete(e, c.id)}
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
