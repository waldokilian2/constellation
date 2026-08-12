"""Plan-preview diagram store regression checks.

Run with: python tests/run_tests.py
Stdlib only (repo convention: no extra deps). Exercises the diagram CRUD
state machine on ``ConversationStore`` (the authoritative panel state
driven by the AI's ``render_diagram`` tool).
"""
from __future__ import annotations
import tempfile
from pathlib import Path

from engine.conversation_store import ConversationStore


def _store():
    """A ConversationStore rooted in a fresh temp project dir."""
    tmp = Path(tempfile.mkdtemp())
    return ConversationStore(tmp)


def test_add_assigns_id_and_persists():
    s = _store()
    c = s.create("p1", "t")
    res = s.mutate_diagrams("p1", c.id, "add", header="Flow A",
                            code="flowchart LR\nA-->B", kind="mermaid")
    assert res["action"] == "add"
    assert res["diagram_id"].startswith("d-")
    assert len(res["diagrams"]) == 1
    d = res["diagrams"][0]
    assert d["header"] == "Flow A"
    assert d["kind"] == "mermaid"
    assert d["code"] == "flowchart LR\nA-->B"
    # Persists across a fresh load.
    assert len(s.get_diagrams("p1", c.id)) == 1


def test_add_with_explicit_id_respected():
    s = _store()
    c = s.create("p1", "t")
    s.mutate_diagrams("p1", c.id, "add", diagram_id="my-id", header="X", code="c")
    res = s.mutate_diagrams("p1", c.id, "add", diagram_id="my-id", header="Y", code="c2")
    # Colliding id must be auto-regenerated, not overwritten by an add.
    assert res["diagram_id"] != "my-id"
    assert len(res["diagrams"]) == 2


def test_get_is_read_only_and_does_not_touch_updated_at():
    s = _store()
    c = s.create("p1", "t")
    before = s.get("p1", c.id).updated_at
    s.mutate_diagrams("p1", c.id, "add", header="X", code="c")
    after_add = s.get("p1", c.id).updated_at
    assert after_add >= before
    res = s.mutate_diagrams("p1", c.id, "get")
    assert len(res["diagrams"]) == 1
    assert res["action"] == "get"
    # get must not bump updated_at.
    assert s.get("p1", c.id).updated_at == after_add


def test_replace_by_id_updates_fields():
    s = _store()
    c = s.create("p1", "t")
    add = s.mutate_diagrams("p1", c.id, "add", header="Old", code="c1")
    did = add["diagram_id"]
    res = s.mutate_diagrams("p1", c.id, "replace", diagram_id=did,
                            header="New", code="c2", kind="html")
    assert len(res["diagrams"]) == 1
    d = res["diagrams"][0]
    assert d["id"] == did
    assert d["header"] == "New"
    assert d["code"] == "c2"
    assert d["kind"] == "html"


def test_replace_without_id_targets_sole_diagram():
    s = _store()
    c = s.create("p1", "t")
    add = s.mutate_diagrams("p1", c.id, "add", header="Solo", code="c1")
    res = s.mutate_diagrams("p1", c.id, "replace", header="Edited", code="c2")
    assert res["diagram_id"] == add["diagram_id"]
    assert res["diagrams"][0]["header"] == "Edited"


def test_replace_with_many_and_bad_id_errors_with_hint():
    s = _store()
    c = s.create("p1", "t")
    s.mutate_diagrams("p1", c.id, "add", header="A", code="c")
    s.mutate_diagrams("p1", c.id, "add", header="B", code="c")
    res = s.mutate_diagrams("p1", c.id, "replace", diagram_id="nope",
                            header="X", code="c")
    assert "error" in res
    assert len(res["diagrams"]) == 2  # nothing changed
    assert "get" in res["error"]


def test_replace_on_empty_falls_back_to_add():
    s = _store()
    c = s.create("p1", "t")
    res = s.mutate_diagrams("p1", c.id, "replace", header="First", code="c1")
    assert res["action"] == "add"
    assert len(res["diagrams"]) == 1


def test_remove_then_clear():
    s = _store()
    c = s.create("p1", "t")
    a = s.mutate_diagrams("p1", c.id, "add", header="A", code="c")
    s.mutate_diagrams("p1", c.id, "add", header="B", code="c")
    res = s.mutate_diagrams("p1", c.id, "remove", diagram_id=a["diagram_id"])
    assert len(res["diagrams"]) == 1
    assert res["diagrams"][0]["header"] == "B"
    # remove of a missing id is a no-op (no error).
    res = s.mutate_diagrams("p1", c.id, "remove", diagram_id="ghost")
    assert len(res["diagrams"]) == 1
    res = s.mutate_diagrams("p1", c.id, "clear")
    assert res["diagrams"] == []


def test_invalid_kind_and_action_handled():
    s = _store()
    c = s.create("p1", "t")
    res = s.mutate_diagrams("p1", c.id, "add", header="X", code="c", kind="svg")
    # Unknown kind falls back to mermaid, not an error.
    assert res["diagrams"][0]["kind"] == "mermaid"
    res = s.mutate_diagrams("p1", c.id, "explode")
    assert "error" in res
    assert res["diagrams"] == []


def test_missing_conversation_returns_error():
    s = _store()
    res = s.mutate_diagrams("p1", "missing", "add", header="X", code="c")
    assert res["diagrams"] == []
    assert "error" in res
    assert s.get_diagrams("p1", "missing") == []


def test_legacy_conversation_without_diagrams_loads_empty():
    """Old conversation JSON files predate the diagrams field."""
    import json
    tmp = Path(tempfile.mkdtemp())
    s = ConversationStore(tmp)
    cdir = tmp / "output" / "projects" / "p1" / "conversations"
    cdir.mkdir(parents=True)
    (cdir / "old.json").write_text(json.dumps({
        "id": "old", "project_id": "p1", "title": "legacy",
        "messages": [], "created_at": "", "updated_at": "",
    }))
    conv = s.get("p1", "old")
    assert conv is not None
    assert conv.diagrams == []
    # And a mutate still works on a legacy conv.
    res = s.mutate_diagrams("p1", "old", "add", header="Hi", code="c")
    assert len(res["diagrams"]) == 1
