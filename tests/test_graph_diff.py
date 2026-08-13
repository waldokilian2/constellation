"""Task 01 regression checks — graph diff & versioning (diff_graphs + snapshots).

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path

def _ep(**over):
    ep = {
        "id": "svc:Class.method",
        "repo": "svc",
        "type": "kafka-consumer",
        "channel": "order-events",
        "class_name": "Class",
        "method": "method",
        "file": "src/Class.java",
        "line": 10,
        "message_type": "",
        "method_type": "",
        "call_tree": None,
        "metrics": {"depth": 1, "total_nodes": 2, "unique_files": 1},
    }
    ep.update(over)
    return ep

def _prod(**over):
    p = {
        "id": "svc:Sender.send",
        "repo": "svc",
        "type": "kafka-producer",
        "channel": "order-events",
        "method": "send",
        "file": "src/Sender.java",
        "line": 5,
        "message_type": "",
    }
    p.update(over)
    return p

def _link(**over):
    l = {"channel": "order-events", "producers": [], "consumers": [], "kind": "message", "verb": ""}
    l.update(over)
    return l

def _graph(eps=(), prods=(), links=()):
    return {
        "repos": ["svc"],
        "repo_roots": {},
        "entry_points": list(eps),
        "producers": list(prods),
        "cross_repo_links": list(links),
        "generated_at": "",
        "engine_version": "0.1.0",
    }

def test_added_removed_entry_points():
    from engine.graph_tools import diff_graphs
    d = diff_graphs(_graph(eps=[_ep(id="a")]), _graph(eps=[_ep(id="b")]))
    assert d["entry_points"]["removed"] == ["a"]
    assert d["entry_points"]["added"] == ["b"]
    assert d["entry_points"]["changed"] == []

def test_changed_metrics():
    from engine.graph_tools import diff_graphs
    old = _graph(eps=[_ep(id="a", metrics={"depth": 1, "total_nodes": 2, "unique_files": 1})])
    new = _graph(eps=[_ep(id="a", metrics={"depth": 3, "total_nodes": 9, "unique_files": 4})])
    d = diff_graphs(old, new)
    assert d["entry_points"]["changed"] == ["a"]
    assert d["entry_points"]["added"] == [] and d["entry_points"]["removed"] == []

def test_changed_call_tree_nodes():
    from engine.graph_tools import diff_graphs
    leaf = {"method": "helper", "file": "A.java", "line": 5, "class_name": "",
            "confidence": "EXTRACTED", "children": []}
    tree_old = {"method": "m", "file": "A.java", "line": 1, "class_name": "",
                "confidence": "EXTRACTED", "children": [leaf]}
    tree_new = {"method": "m", "file": "A.java", "line": 1, "class_name": "",
                "confidence": "EXTRACTED", "children": []}
    d = diff_graphs(_graph(eps=[_ep(id="a", call_tree=tree_old)]),
                    _graph(eps=[_ep(id="a", call_tree=tree_new)]))
    assert d["entry_points"]["changed"] == ["a"]

def test_unchanged_graph_empty_diff():
    from engine.graph_tools import diff_graphs
    g = _graph(eps=[_ep(id="a")], prods=[_prod()], links=[_link()])
    d = diff_graphs(g, g)
    assert d["entry_points"] == {"added": [], "removed": [], "changed": []}
    assert d["producers"] == {"added": [], "removed": [], "changed": []}
    assert d["cross_repo_links"] == {"added": [], "removed": []}
    assert all(v == 0 for v in d["summary"].values()), d["summary"]

def test_producer_and_link_changes():
    from engine.graph_tools import diff_graphs
    old = _graph(prods=[_prod()], links=[_link()])
    new = _graph()
    d = diff_graphs(old, new)
    assert d["producers"]["removed"] == ["svc:Sender.send"]
    assert d["cross_repo_links"]["removed"] == ["order-events"]
    assert d["summary"]["producers_removed"] == 1
    assert d["summary"]["links_removed"] == 1

def test_store_persists_snapshot_chain():
    from engine.project_store import ProjectStore
    with tempfile.TemporaryDirectory() as td:
        store = ProjectStore(Path(td))
        pid = "t-1"
        store.project_dir(pid).mkdir(parents=True)
        first = _graph(eps=[_ep(id="a")])
        store._persist_graph(pid, first)
        assert store.latest_snapshot(pid) is None
        assert store.list_snapshots(pid) == []
        assert not (store.project_dir(pid) / "last_diff.json").exists()
        store._persist_graph(pid, _graph(eps=[_ep(id="b")]))
        snaps = sorted(store.snapshots_dir(pid).glob("*.json"))
        assert len(snaps) == 1, "exactly one snapshot after two persists"
        assert json.loads(snaps[0].read_text())["entry_points"][0]["id"] == "a"
        assert store.latest_snapshot(pid)["entry_points"][0]["id"] == "a"
        assert not hasattr(ProjectStore, "load_last_diff")
        assert not hasattr(ProjectStore, "last_diff_path")

def test_snapshot_pruning_keeps_last_ten():
    from engine.project_store import ProjectStore
    with tempfile.TemporaryDirectory() as td:
        store = ProjectStore(Path(td))
        pid = "t-2"
        store.project_dir(pid).mkdir(parents=True)
        for i in range(15):
            store._save_snapshot(pid, {"n": i})
        snaps = sorted(store.snapshots_dir(pid).glob("*.json"))
        assert len(snaps) == ProjectStore.SNAPSHOT_LIMIT == 10
        assert json.loads(snaps[-1].read_text())["n"] == 14

def test_diff_tool_registered_everywhere():
    from engine.graph_tools import TOOL_DEFINITIONS, execute_tool, _filter_args
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "diff_graphs" in names
    result = execute_tool(_graph(eps=[_ep(id="a")]), "diff_graphs",
                          {"old_graph": _graph(eps=[_ep(id="b")])})
    assert result["entry_points"]["removed"] == ["b"]
    assert result["entry_points"]["added"] == ["a"]
    assert _filter_args("diff_graphs", {"old_graph": {}, "bogus": 1}) == {"old_graph": {}}
