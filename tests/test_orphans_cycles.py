"""Issue #14 regression checks — orphan detection & repo dependency cycles.

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps). No engine run
needed: find_orphans / find_cycles are pure functions over a graph dict, so we
hand-build a tiny graph and assert directly.
"""
from __future__ import annotations
from engine.graph_tools import find_orphans, find_cycles, execute_tool


def _graph():
    """A 2-repo graph with one orphan producer, one orphan consumer, and an
    A->B->A cycle.

    Wired (linked, NOT orphans):
      - "orders"   : produced by b, consumed by a   -> edge b -> a
      - "receipts" : produced by a, consumed by b   -> edge a -> b   (closes the cycle)

    Orphans (only one side exists):
      - "shipments": produced by a, consumed by nobody  -> orphan producer
      - "payments" : consumed by b, produced by nobody  -> orphan consumer
    """
    return {
        "repos": ["a", "b"],
        "entry_points": [
            {"id": "a:OrdersConsumer.handle", "repo": "a", "type": "kafka-consumer",
             "channel": "orders", "method": "handle", "class_name": "OrdersConsumer",
             "file": "OrdersConsumer.java", "line": 10},
            {"id": "b:ReceiptsConsumer.onReceipt", "repo": "b", "type": "kafka-consumer",
             "channel": "receipts", "method": "onReceipt", "class_name": "ReceiptsConsumer",
             "file": "ReceiptsConsumer.java", "line": 20},
            {"id": "b:PayListener.onPay", "repo": "b", "type": "rabbitmq-consumer",
             "channel": "payments", "method": "onPay", "class_name": "PayListener",
             "file": "PayListener.java", "line": 30},
        ],
        "producers": [
            {"id": "b:OrderSvc.publish", "repo": "b", "type": "kafka-producer",
             "channel": "orders", "method": "publish", "file": "OrderSvc.java", "line": 5},
            {"id": "a:ReceiptSvc.emit", "repo": "a", "type": "kafka-producer",
             "channel": "receipts", "method": "emit", "file": "ReceiptSvc.java", "line": 7},
            {"id": "a:ShipSvc.send", "repo": "a", "type": "kafka-producer",
             "channel": "shipments", "method": "send", "file": "ShipSvc.java", "line": 9},
        ],
        "cross_repo_links": [
            {"channel": "orders", "producers": ["b:OrderSvc.publish"],
             "consumers": ["a:OrdersConsumer.handle"], "kind": "message"},
            {"channel": "receipts", "producers": ["a:ReceiptSvc.emit"],
             "consumers": ["b:ReceiptsConsumer.onReceipt"], "kind": "message"},
        ],
    }


# ── find_orphans ──────────────────────────────────────────────────

def test_orphan_producer_detected():
    res = find_orphans(_graph())
    chans = [p["channel"] for p in res["orphan_producers"]]
    assert chans == ["shipments"], f"expected only 'shipments' orphaned, got {chans}"
    prod = res["orphan_producers"][0]
    assert prod["repo"] == "a" and prod["method"] == "send" and prod["line"] == 9


def test_orphan_consumer_detected():
    res = find_orphans(_graph())
    chans = [c["channel"] for c in res["orphan_consumers"]]
    assert chans == ["payments"], f"expected only 'payments' orphaned, got {chans}"
    cons = res["orphan_consumers"][0]
    assert cons["repo"] == "b" and cons["method"] == "onPay"


def test_linked_channels_not_flagged():
    """orders/receipts have both sides — they must NOT be orphans."""
    res = find_orphans(_graph())
    all_chans = {p["channel"] for p in res["orphan_producers"]} | \
                {c["channel"] for c in res["orphan_consumers"]}
    assert "orders" not in all_chans and "receipts" not in all_chans


def test_orphans_summary_counts():
    s = find_orphans(_graph())["summary"]
    assert s["orphan_producers"] == 1 and s["orphan_consumers"] == 1
    assert set(s["orphan_channels"]) == {"shipments", "payments"}


def test_orphans_does_not_mutate_graph():
    g = _graph()
    before = len(g["cross_repo_links"])
    find_orphans(g)
    assert len(g["cross_repo_links"]) == before, "find_orphans must not mutate the graph"


def test_http_call_producer_is_not_a_message_orphan():
    """An HTTP_CALL producer's channel is a REST path, not a message channel —
    it must never appear as a message-channel orphan."""
    g = {
        "repos": ["a"],
        "entry_points": [],
        "producers": [
            {"id": "a:Client.call", "repo": "a", "type": "http-call",
             "channel": "/api/orders/123", "method": "call", "file": "C.java", "line": 1},
        ],
        "cross_repo_links": [],
    }
    res = find_orphans(g)
    assert res["orphan_producers"] == [], "http-call producers must be excluded"
    assert res["summary"]["orphan_producers"] == 0


def test_orphans_empty_graph_safe():
    res = find_orphans({})
    assert res["orphan_producers"] == [] and res["orphan_consumers"] == []
    assert res["summary"]["orphan_producers"] == 0


# ── find_cycles ───────────────────────────────────────────────────

def test_cycle_a_to_b_to_a_detected():
    cyc = find_cycles(_graph())["cycles"]
    assert len(cyc) == 1, f"expected 1 cycle, got {cyc}"
    c = cyc[0]
    # closed path a -> b -> a (or b -> a -> b); length 2, repos {a, b}
    assert c["length"] == 2
    assert set(c["repos"]) == {"a", "b"}
    assert c["repos"][0] == c["repos"][-1], "repos should be a closed path"
    assert set(c["channels"]) == {"orders", "receipts"}


def test_cycles_summary():
    s = find_cycles(_graph())["summary"]
    assert s["cycle_count"] == 1
    assert set(s["repos_in_cycles"]) == {"a", "b"}


def test_cycles_does_not_mutate_graph():
    g = _graph()
    before = [dict(l) for l in g["cross_repo_links"]]
    find_cycles(g)
    assert g["cross_repo_links"] == before, "find_cycles must not mutate the graph"


def test_cycles_self_loop_ignored():
    """A producer and consumer in the SAME repo is not a cross-repo cycle."""
    g = {
        "repos": ["a"],
        "entry_points": [],
        "producers": [],
        "cross_repo_links": [
            {"channel": "x", "producers": ["a:P.p"], "consumers": ["a:C.c"], "kind": "message"},
        ],
    }
    assert find_cycles(g)["cycles"] == [], "intra-repo links must not form a cycle"


def test_cycles_acyclic_safe():
    """A DAG (a -> b -> c, no back-edge) has no cycles."""
    g = {
        "repos": ["a", "b", "c"],
        "entry_points": [],
        "producers": [],
        "cross_repo_links": [
            {"channel": "x", "producers": ["a:P.p"], "consumers": ["b:C.c"], "kind": "message"},
            {"channel": "y", "producers": ["b:P2.p"], "consumers": ["c:C2.c"], "kind": "message"},
        ],
    }
    assert find_cycles(g)["cycles"] == []


def test_cycles_empty_graph_safe():
    res = find_cycles({})
    assert res["cycles"] == [] and res["summary"]["cycle_count"] == 0


# ── execute_tool dispatch ─────────────────────────────────────────

def test_execute_tool_dispatch_orphans():
    res = execute_tool(_graph(), "find_orphans", {})
    assert res["summary"]["orphan_producers"] == 1


def test_execute_tool_dispatch_cycles():
    res = execute_tool(_graph(), "find_cycles", {})
    assert res["summary"]["cycle_count"] == 1


def test_execute_tool_unknown_still_errors():
    res = execute_tool(_graph(), "does_not_exist", {})
    assert "error" in res
