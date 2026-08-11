"""Dead-code detection regression checks.

Run with: python tests/run_tests.py
Stdlib only (repo convention: no extra deps). The tool-level tests use a
hand-built graph dict (no engine run); one integration test runs the engine on
the seed repos to exercise the full reachability pass.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "repos"


# ── _is_trivial_definition ───────────────────────────────────────

def test_trivial_definition_filters_accessors():
    from engine.call_graph import _is_trivial_definition as t
    assert t("getName") and t("setActive") and t("isActive")
    assert t("toString") and t("hashCode") and t("equals")
    # genuinely interesting names are NOT filtered
    assert not t("build") and not t("handle") and not t("process") and not t("send")


# ── find_dead_code on a hand-built graph ──────────────────────────

def _graph():
    """2 repos:
      - unreachable method pre-populated (engine normally computes it)
      - one thin handler (total_nodes 1), one healthy handler (total_nodes 5)
      - repo "lonely" has no cross-repo link → isolated
    """
    return {
        "repos": ["a", "b", "lonely"],
        "entry_points": [
            {"id": "a:Ctrl.thin", "repo": "a", "type": "rest-endpoint", "channel": "/thin",
             "method": "thin", "metrics": {"total_nodes": 1, "depth": 0}},
            {"id": "a:Ctrl.full", "repo": "a", "type": "rest-endpoint", "channel": "/full",
             "method": "full", "metrics": {"total_nodes": 5, "depth": 2}},
        ],
        "producers": [],
        "cross_repo_links": [
            {"channel": "x", "producers": ["a:P.p"], "consumers": ["b:C.c"], "kind": "message"},
        ],
        "methods_total": 10,
        "unreachable_methods": [
            {"id": "b:Dead.doStuff", "repo": "b", "class_name": "Dead", "method": "doStuff",
             "file": "Dead.java", "line": 9},
        ],
    }


def test_unreachable_methods_surfaced():
    d = __import__("engine.graph_tools", fromlist=["find_dead_code"]).find_dead_code(_graph())
    ids = [m["id"] for m in d["unreachable_methods"]]
    assert ids == ["b:Dead.doStuff"], ids


def test_thin_handler_detected_healthy_not():
    d = __import__("engine.graph_tools", fromlist=["find_dead_code"]).find_dead_code(_graph())
    thin = [h["method"] for h in d["thin_handlers"]]
    assert thin == ["thin"], thin  # 'full' (5 nodes) must NOT be flagged


def test_thin_flag_overrides_node_count():
    """The engine 'thin' flag (genuine no-op) takes precedence over the
    total_nodes heuristic — so a body with calls isn't a false thin hit, and a
    flagged stub stays flagged regardless of node count."""
    from engine.graph_tools import find_dead_code
    g = {
        "repos": ["a"],
        "entry_points": [
            {"id": "a:Stub.s", "repo": "a", "type": "scheduled-task", "channel": "cron",
             "method": "s", "metrics": {"total_nodes": 5, "depth": 2, "thin": True}},
            {"id": "a:Real.r", "repo": "a", "type": "kafka-consumer", "channel": "t",
             "method": "r", "metrics": {"total_nodes": 1, "depth": 0, "thin": False}},
        ],
        "producers": [], "cross_repo_links": [],
    }
    d = find_dead_code(g)
    assert [h["method"] for h in d["thin_handlers"]] == ["s"]


def test_isolated_repos():
    d = __import__("engine.graph_tools", fromlist=["find_dead_code"]).find_dead_code(_graph())
    assert d["isolated_repos"] == ["lonely"], d["isolated_repos"]


def test_summary_and_method_index_available():
    from engine.graph_tools import find_dead_code
    s = find_dead_code(_graph())["summary"]
    assert s["unreachable_methods"] == 1 and s["methods_total"] == 10
    assert s["thin_handlers"] == 1 and s["isolated_repos"] == 1
    assert s["method_index_available"] is True


def test_graceful_without_method_index():
    """Old graph (no methods_total / unreachable_methods) still reports the
    light signals and marks method_index_available False."""
    from engine.graph_tools import find_dead_code
    g = {
        "repos": ["a", "z"],
        "entry_points": [{"id": "a:X.x", "repo": "a", "type": "rest-endpoint",
                          "channel": "/x", "method": "x", "metrics": {"total_nodes": 1}}],
        "producers": [], "cross_repo_links": [],
    }
    d = find_dead_code(g)
    assert d["unreachable_methods"] == []
    assert d["summary"]["method_index_available"] is False
    assert d["summary"]["methods_total"] == 0
    assert len(d["thin_handlers"]) == 1
    assert d["isolated_repos"] == ["a", "z"]


def test_find_dead_code_does_not_mutate_graph():
    from engine.graph_tools import find_dead_code
    g = _graph()
    before = json.dumps(g, sort_keys=True)
    find_dead_code(g)
    assert json.dumps(g, sort_keys=True) == before, "find_dead_code must not mutate the graph"


def test_execute_tool_dispatch():
    from engine.graph_tools import execute_tool
    res = execute_tool(_graph(), "find_dead_code", {})
    assert res["summary"]["unreachable_methods"] == 1
    assert res["summary"]["method_index_available"] is True


# ── Engine integration: full reachability on seed repos ───────────

def _run_engine(repos, out):
    subprocess.run(
        [sys.executable, "-m", "engine.constellation", *repos, "--output", str(out)],
        cwd=REPO, check=True, capture_output=True,
    )
    return json.loads(out.read_text())


def test_reachability_on_seed_repos():
    repos = [str(FIXTURES / r) for r in ("order-service", "fulfillment-service", "notification-service")]
    with tempfile.TemporaryDirectory() as td:
        g = _run_engine(repos, Path(td) / "g.json")

    # The engine serialized the reachability phase output.
    assert g["methods_total"] > 0, "engine should index methods"
    assert isinstance(g["unreachable_methods"], list)
    assert len(g["unreachable_methods"]) < g["methods_total"], "unreachable must be a subset"

    # No entry-point method may appear in unreachable — entry points are roots.
    ep_ids = {ep["id"] for ep in g["entry_points"]}
    unreachable_ids = {m["id"] for m in g["unreachable_methods"]}
    overlap = ep_ids & unreachable_ids
    assert not overlap, f"entry-point methods must not be flagged dead: {overlap}"

    # Tool reads the same precomputed data.
    from engine.graph_tools import find_dead_code
    s = find_dead_code(g)["summary"]
    assert s["method_index_available"] is True
    assert s["unreachable_methods"] == len(g["unreachable_methods"])
