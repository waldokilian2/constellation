"""Conversation kind-scoping regression checks.

Run with: python tests/run_tests.py
Stdlib only (repo convention: no extra deps). Verifies that the per-page
assistant ("chat") and the AI Change Planner ("planner") keep SEPARATE
conversation histories — they have different system prompts and must never
share or even see each other's conversations.
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path

from engine.conversation_store import ConversationStore


def _store():
    """A ConversationStore rooted in a fresh temp project dir."""
    tmp = Path(tempfile.mkdtemp())
    return ConversationStore(tmp)


def test_create_records_kind():
    s = _store()
    chat = s.create("p1", "c", kind="chat")
    planner = s.create("p1", "p", kind="planner")
    assert chat.kind == "chat"
    assert planner.kind == "planner"
    # Default kind is chat.
    assert s.create("p1", "x").kind == "chat"
    # Unknown kind falls back to chat, never persisted as garbage.
    assert s.create("p1", "x", kind="weird").kind == "chat"


def test_list_scopes_by_kind():
    s = _store()
    c1 = s.create("p1", "chat-1", kind="chat")
    p1 = s.create("p1", "plan-1", kind="planner")
    c2 = s.create("p1", "chat-2", kind="chat")

    chat_ids = {c["id"] for c in s.list("p1", kind="chat")}
    plan_ids = {c["id"] for c in s.list("p1", kind="planner")}
    all_ids = {c["id"] for c in s.list("p1")}

    assert chat_ids == {c1.id, c2.id}
    assert plan_ids == {p1.id}
    # Unscoped list still returns everything (back-compat).
    assert all_ids == {c1.id, c2.id, p1.id}
    # Each meta entry carries its kind.
    assert all(c.get("kind") == "chat" for c in s.list("p1", kind="chat"))
    assert all(c.get("kind") == "planner" for c in s.list("p1", kind="planner"))


def test_get_or_create_default_is_per_kind():
    s = _store()
    chat_default = s.get_or_create_default("p1", kind="chat")
    plan_default = s.get_or_create_default("p1", kind="planner")

    # Two distinct defaults, each on its own surface.
    assert chat_default.id != plan_default.id
    assert chat_default.kind == "chat"
    assert plan_default.kind == "planner"

    # Calling again returns the SAME per-kind default (no duplication, and
    # the chat default never leaks into the planner surface or vice-versa).
    assert s.get_or_create_default("p1", kind="chat").id == chat_default.id
    assert s.get_or_create_default("p1", kind="planner").id == plan_default.id
    assert len(s.list("p1", kind="chat")) == 1
    assert len(s.list("p1", kind="planner")) == 1


def test_kind_round_trips_through_save_and_get():
    s = _store()
    planner = s.create("p1", "p", kind="planner")
    # Re-load from disk.
    reloaded = s.get("p1", planner.id)
    assert reloaded is not None
    assert reloaded.kind == "planner"
    # to_dict / meta surface the kind too.
    assert reloaded.to_dict()["kind"] == "planner"
    assert reloaded.meta()["kind"] == "planner"


def test_legacy_conversation_without_kind_treated_as_chat():
    """Old conversation JSON files predate the kind field → chat surface."""
    tmp = Path(tempfile.mkdtemp())
    s = ConversationStore(tmp)
    cdir = tmp / "output" / "projects" / "p1" / "conversations"
    cdir.mkdir(parents=True)
    (cdir / "old.json").write_text(json.dumps({
        "id": "old", "project_id": "p1", "title": "legacy",
        "messages": [], "created_at": "", "updated_at": "",
    }))
    # Legacy loads as chat, so a planner list excludes it but chat includes it.
    conv = s.get("p1", "old")
    assert conv.kind == "chat"
    assert [c["id"] for c in s.list("p1", kind="planner")] == []
    assert [c["id"] for c in s.list("p1", kind="chat")] == ["old"]
