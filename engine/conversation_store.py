"""
Conversation persistence layer.

Stores conversations as JSON files under output/projects/<pid>/conversations/.
Each conversation carries the full OpenAI-format message history (including
tool_calls + tool result messages) so multi-turn chat survives refresh,
crash, and tab-switch.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
import os
import shutil
import time
import uuid


@dataclass
class Conversation:
    id: str                          # uuid
    project_id: str                  # owning project
    title: str                       # auto-generated from first user msg
    messages: list[dict] = field(default_factory=list)
    # AI-managed diagrams shown in the planner's right-side preview panel.
    # Authoritative current state — mutated via ``mutate_diagrams``. Each
    # entry: ``{id, header, kind, code, updated_at}`` where kind is
    # ``"mermaid"`` or ``"html"``.
    diagrams: list[dict] = field(default_factory=list)
    # Which chat surface owns this conversation: ``"chat"`` (the per-page
    # assistant) or ``"planner"`` (the AI Change Planner). The two surfaces
    # must NOT share history — they have different system prompts — so
    # create/list/default are scoped by kind.
    kind: str = "chat"
    created_at: str = ""             # ISO 8601
    updated_at: str = ""             # ISO 8601

    def to_dict(self) -> dict:
        """Full conversation as a plain dict (JSON-serializable)."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "messages": self.messages,
            "diagrams": self.diagrams,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def meta(self) -> dict:
        """Metadata-only view (no message bodies) for list endpoints."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
        }


class ConversationStore:
    """Create, load, save, and delete conversation JSON files per project."""

    # Valid chat surfaces that own a conversation. Drives kind-scoping in
    # create/list/get_or_create_default so the page chat and the planner keep
    # independent histories and system prompts.
    _CONVERSATION_KINDS = ("chat", "planner")

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._conv_dir_name = "conversations"

    # ── helpers ─────────────────────────────────────────────────────

    def _conv_dir(self, project_id: str) -> Path:
        return self._base_dir / "output" / "projects" / project_id / self._conv_dir_name

    def _file(self, project_id: str, conv_id: str) -> Path:
        return self._conv_dir(project_id) / f"{conv_id}.json"

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ── CRUD ────────────────────────────────────────────────────────

    def create(self, project_id: str, title: str = "", kind: str = "chat") -> Conversation:
        """Create a new conversation and persist it. Returns the new conv.

        ``kind`` separates the two chat surfaces (``"chat"`` vs
        ``"planner"``) so they keep independent histories and system prompts.
        Unknown values fall back to ``"chat"``.
        """
        conv_id = str(uuid.uuid4())
        now = self._now()
        conv = Conversation(
            id=conv_id,
            project_id=project_id,
            title=title or "New conversation",
            messages=[],
            kind=kind if kind in self._CONVERSATION_KINDS else "chat",
            created_at=now,
            updated_at=now,
        )
        self.save(project_id, conv)
        return conv

    def get(self, project_id: str, conv_id: str) -> Optional[Conversation]:
        """Load a conversation from its JSON file (None if missing)."""
        path = self._file(project_id, conv_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(errors="replace"))
        return Conversation(
            id=data["id"],
            project_id=data["project_id"],
            title=data.get("title", ""),
            messages=data.get("messages", []),
            diagrams=data.get("diagrams", []),
            # Legacy files predate the kind field → treat as "chat".
            kind=data.get("kind", "chat") or "chat",
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def list(self, project_id: str, kind: str = "") -> list[dict]:
        """List conversations for a project (metadata only, newest first).

        When ``kind`` is given (``"chat"`` / ``"planner"``), only that
        surface's conversations are returned — so the page chat and the
        planner never see each other's history. Empty/unknown → all kinds
        (back-compat for unscoped callers).
        """
        conv_dir = self._conv_dir(project_id)
        if not conv_dir.exists():
            return []
        scoped = kind if kind in self._CONVERSATION_KINDS else ""
        results = []
        for path in conv_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(errors="replace"))
            except (json.JSONDecodeError, KeyError):
                continue
            if scoped and (data.get("kind", "chat") or "chat") != scoped:
                continue
            results.append({
                "id": data["id"],
                "title": data.get("title", ""),
                "kind": data.get("kind", "chat") or "chat",
                "updated_at": data.get("updated_at", ""),
                "created_at": data.get("created_at", ""),
                "message_count": len(data.get("messages", [])),
            })
        # Newest first by updated_at (the hook loads list[0] as the active chat,
        # so this must be the most-recently-touched conversation, not an
        # arbitrary one ordered by UUID filename).
        results.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return results

    def save(self, project_id: str, conv: Conversation) -> None:
        """Write a conversation to its JSON file (atomic: temp → rename)."""
        conv.updated_at = self._now()
        conv_dir = self._conv_dir(project_id)
        conv_dir.mkdir(parents=True, exist_ok=True)
        target = self._file(project_id, conv.id)
        tmp = target.with_suffix(".tmp")
        data = {
            "id": conv.id,
            "project_id": conv.project_id,
            "title": conv.title,
            "messages": conv.messages,
            "diagrams": conv.diagrams,
            "kind": conv.kind,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        }
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(target))

    def delete(self, project_id: str, conv_id: str) -> bool:
        """Remove a conversation file. Returns True if it existed."""
        path = self._file(project_id, conv_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def get_or_create_default(self, project_id: str, kind: str = "chat") -> Conversation:
        """Return the default conversation for a surface; create if absent.

        Scoped by ``kind`` (``"chat"`` / ``"planner"``): each surface gets
        its own most-recently-touched conversation, never the other's.
        """
        convs = self.list(project_id, kind=kind)
        if convs:
            cid = convs[0]["id"]
            conv = self.get(project_id, cid)
            if conv:
                return conv
        return self.create(project_id, title="Default conversation", kind=kind)

    def replace_messages(
        self, project_id: str, conv_id: str, messages: list[dict]
    ) -> Optional[Conversation]:
        """Overwrite a conversation's full message list and save atomically.

        Used after a streaming turn completes: the server rebuilt the
        authoritative history (user + assistant + tool messages) and writes
        it back in one shot. Also keeps the auto-title in sync.
        """
        conv = self.get(project_id, conv_id)
        if not conv:
            return None
        conv.messages = messages
        # Auto-title: once a real message lands, replace any placeholder title
        # with a truncated version of the first user message. Covers every
        # placeholder the UI/seed creates so the chat list shows meaningful
        # names instead of "Default conversation" forever.
        if conv.title in _PLACEHOLDER_TITLES and messages:
            for m in messages:
                if m.get("role") == "user":
                    conv.title = _title_from_text(m.get("content", ""))
                    break
        self.save(project_id, conv)
        return conv

    # ── Plan-preview diagrams (AI-managed via the render_diagram tool) ──

    # Valid render modes for a diagram body.
    _DIAGRAM_KINDS = ("mermaid", "html")
    # Valid CRUD actions accepted by ``mutate_diagrams``.
    _DIAGRAM_ACTIONS = ("add", "replace", "remove", "get", "clear")

    def get_diagrams(self, project_id: str, conv_id: str) -> list[dict]:
        """Return the conversation's current panel diagrams (empty if missing)."""
        conv = self.get(project_id, conv_id)
        return list(conv.diagrams) if conv else []

    def mutate_diagrams(
        self,
        project_id: str,
        conv_id: str,
        action: str = "add",
        diagram_id: str = "",
        header: str = "",
        code: str = "",
        kind: str = "mermaid",
    ) -> dict:
        """Apply one diagram CRUD op on a conversation's preview panel.

        Diagram state is **authoritative** here (persisted on the
        conversation), so ``get``/``remove``/``replace`` reflect the real
        current panel — not a guess. Each diagram:
        ``{id, header, kind, code, updated_at}``.

        Actions:
          * ``add``     — append a new diagram (id auto-generated if
                          omitted or already taken).
          * ``replace`` — update an existing diagram by id. If the id is
                          omitted and exactly one diagram exists, that one
                          is updated; with zero diagrams it falls back to
                          ``add``; with many and a wrong id it returns the
                          current list with an ``error`` hint.
          * ``remove``  — delete a diagram by id (no-op if absent).
          * ``clear``   — delete every diagram.
          * ``get``     — read-only; returns the current list.

        Returns ``{"action", "diagram_id", "diagrams": [...]}`` (the full
        current list after the op so callers can mirror deterministically).
        """
        action = (action or "add").strip().lower()
        if action not in self._DIAGRAM_ACTIONS:
            return {
                "action": action, "diagram_id": diagram_id, "diagrams": [],
                "error": f"Unknown action '{action}'. Use one of {self._DIAGRAM_ACTIONS}.",
            }

        conv = self.get(project_id, conv_id)
        if not conv:
            return {
                "action": action, "diagram_id": diagram_id, "diagrams": [],
                "error": f"Conversation '{conv_id}' not found.",
            }

        diagrams: list[dict] = [dict(d) for d in (conv.diagrams or [])]
        now = self._now()
        kind = kind if kind in self._DIAGRAM_KINDS else "mermaid"
        changed = False
        resolved_id = diagram_id

        def _find(target_id: str) -> int:
            for i, d in enumerate(diagrams):
                if d.get("id") == target_id:
                    return i
            return -1

        if action == "add":
            if not resolved_id or _find(resolved_id) != -1:
                resolved_id = "d-" + uuid.uuid4().hex[:10]
            diagrams.append({
                "id": resolved_id,
                "header": (header or "Diagram").strip(),
                "kind": kind,
                "code": code or "",
                "updated_at": now,
            })
            changed = True

        elif action == "replace":
            i = _find(resolved_id) if resolved_id else -1
            if i == -1:
                if not resolved_id and len(diagrams) == 1:
                    i = 0
                    resolved_id = diagrams[0].get("id", "")
                elif not resolved_id and len(diagrams) == 0:
                    # Nothing to replace → add instead (graceful for first edit).
                    return self.mutate_diagrams(
                        project_id, conv_id, "add",
                        diagram_id="", header=header, code=code, kind=kind,
                    )
                else:
                    return {
                        "action": "replace", "diagram_id": resolved_id,
                        "diagrams": diagrams,
                        "error": (
                            "No matching diagram for that id. Call "
                            "render_diagram with action 'get' to list current "
                            "ids, then 'replace'."
                        ),
                    }
            d = diagrams[i]
            if header:
                d["header"] = header.strip()
            if code:
                d["code"] = code
            d["kind"] = kind
            d["updated_at"] = now
            changed = True

        elif action == "remove":
            i = _find(resolved_id)
            if i != -1:
                diagrams.pop(i)
                changed = True

        elif action == "clear":
            diagrams = []
            changed = True

        # action == "get" → read-only, no change.

        if changed:
            conv.diagrams = diagrams
            self.save(project_id, conv)

        return {
            "action": action,
            "diagram_id": resolved_id,
            "diagrams": diagrams,
        }


# Placeholder titles that ``replace_messages`` overwrites with a real,
# first-message-derived title. Every UI entry point + the seed create
# conversations with one of these, so they all auto-rename on first message.
_PLACEHOLDER_TITLES = {"", "New conversation", "Default conversation"}


def _title_from_text(text: str) -> str:
    """Derive a short conversation title from the first user message."""
    flat = " ".join((text or "").split())
    return flat[:60] + ("…" if len(flat) > 60 else "")