"""Conversation auto-title regression checks.

Run with: python tests/run_tests.py
Stdlib only (repo convention: no extra deps). Verifies that a conversation
gets renamed to a truncated version of its first user message as soon as a
real message lands — for every placeholder title the UI/seed create, and
that a user-set title is preserved.
"""
from __future__ import annotations
from pathlib import Path
import tempfile

from engine.conversation_store import ConversationStore


def _store():
    tmp = Path(tempfile.mkdtemp())
    return ConversationStore(tmp)


def _title_after_first_msg(s, kind="chat", create_title="Default conversation"):
    c = s.create("p1", create_title, kind=kind)
    s.replace_messages("p1", c.id, [
        {"role": "user", "content": "How does order-service publish to Kafka?"},
        {"role": "assistant", "content": "..."},
    ])
    return s.get("p1", c.id).title


def test_default_conversation_title_renamed():
    s = _store()
    title = _title_after_first_msg(s, create_title="Default conversation")
    assert title == "How does order-service publish to Kafka?"


def test_new_conversation_title_renamed():
    s = _store()
    title = _title_after_first_msg(s, create_title="New conversation")
    assert title.startswith("How does order-service")


def test_empty_title_renamed():
    s = _store()
    # create("") → server fills "New conversation"; emulate an empty-ish title
    # by exercising the placeholder set directly via a chat-kind create.
    c = s.create("p1", "")
    s.replace_messages("p1", c.id, [{"role": "user", "content": "hello world"}])
    assert s.get("p1", c.id).title == "hello world"


def test_long_first_message_is_truncated():
    s = _store()
    long_msg = "x" * 200
    c = s.create("p1", "Default conversation")
    s.replace_messages("p1", c.id, [{"role": "user", "content": long_msg}])
    title = s.get("p1", c.id).title
    assert title.endswith("…")
    # 60 chars of content + the ellipsis.
    assert len(title) == 61


def test_real_title_is_preserved():
    """A non-placeholder title must NOT be overwritten on later turns."""
    s = _store()
    c = s.create("p1", "My custom title")
    s.replace_messages("p1", c.id, [{"role": "user", "content": "anything"}])
    assert s.get("p1", c.id).title == "My custom title"


def test_renamed_only_from_first_user_message():
    """A system/tool message before the first user msg must not become the title."""
    s = _store()
    c = s.create("p1", "Default conversation")
    s.replace_messages("p1", c.id, [
        {"role": "system", "content": "system prompt should not be the title"},
        {"role": "assistant", "content": "nor this"},
        {"role": "user", "content": "the real first question"},
    ])
    assert s.get("p1", c.id).title == "the real first question"


def test_title_updates_for_planner_kind_too():
    s = _store()
    title = _title_after_first_msg(s, kind="planner", create_title="Default conversation")
    assert title == "How does order-service publish to Kafka?"
