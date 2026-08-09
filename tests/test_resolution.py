"""Regression checks — import-aware / local-variable call resolution.

Verifies that chained calls on locals and interface-typed receivers resolve to
concrete definitions (EXTRACTED / AMBIGUOUS) instead of falling back to
INFERRED. Run with: python tests/run_tests.py
"""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "repos"


def run_engine(repos, out):
    subprocess.run(
        [sys.executable, "-m", "engine.constellation", *repos, "--output", str(out)],
        cwd=REPO, check=True, capture_output=True,
    )
    return json.loads(out.read_text())


def _find_tree(graph, class_name, channel=None, method=None):
    for ep in graph["entry_points"]:
        if ep["class_name"] != class_name:
            continue
        if channel is not None and ep["channel"] != channel:
            continue
        if method is not None and ep["method"] != method:
            continue
        return ep["call_tree"]
    raise AssertionError(f"no entry point {class_name} {channel} {method}")


def _all_nodes(tree):
    yield tree
    for c in tree.get("children", []):
        yield from _all_nodes(c)


def _confidence_counts(tree):
    out = {}
    for n in _all_nodes(tree):
        conf = n.get("confidence", "")
        out[conf] = out.get(conf, 0) + 1
    return out


def test_local_variable_call_resolves():
    """A call on a local var (Order order = ...; order.validate()) must resolve."""
    repos = [str(FIXTURES / r) for r in ("order-service", "fulfillment-service", "notification-service")]
    with tempfile.TemporaryDirectory() as td:
        g = run_engine(repos, Path(td) / "g.json")

    tree = _find_tree(g, "OrderController", channel="/api/orders")
    nodes = list(_all_nodes(tree))
    validate = [n for n in nodes if n["method"] == "order.validate"]
    assert validate, "expected order.validate node in createOrder tree"
    assert validate[0]["confidence"] == "EXTRACTED", \
        f"local chained call should resolve to EXTRACTED, got {validate[0]['confidence']}"


def test_interface_receiver_is_ambiguous():
    """A call through an interface with two impls resolves to AMBIGUOUS."""
    repos = [str(FIXTURES / r) for r in ("order-service", "fulfillment-service", "notification-service")]
    with tempfile.TemporaryDirectory() as td:
        g = run_engine(repos, Path(td) / "g.json")

    tree = _find_tree(g, "ShipmentTrackingConsumer")
    nodes = list(_all_nodes(tree))
    notifier = [n for n in nodes if n["method"] == "notifier.send"]
    assert notifier, "expected notifier.send node"
    assert notifier[0]["confidence"] == "AMBIGUOUS", \
        f"interface dispatch should be AMBIGUOUS, got {notifier[0]['confidence']}"


def test_unindexed_type_stays_inferred():
    """A call to a type absent from the repo must stay INFERRED."""
    repos = [str(FIXTURES / r) for r in ("order-service", "fulfillment-service", "notification-service")]
    with tempfile.TemporaryDirectory() as td:
        g = run_engine(repos, Path(td) / "g.json")

    tree = _find_tree(g, "ShipmentTrackingConsumer")
    nodes = list(_all_nodes(tree))
    inferred = [n for n in nodes if n["confidence"] == "INFERRED"]
    assert inferred, "expected at least one INFERRED node (TemplateEngine.render)"
    assert any("render" in n["method"] for n in inferred), \
        f"expected TemplateEngine.render inferred, got {[n['method'] for n in inferred]}"
