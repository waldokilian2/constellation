"""Tests for engine/context_builder.py — page-scoped system prompts.

Run with: python tests/run_tests.py test_context_builder

These are stdlib-only and cover the Code Issues system prompt: that it
embeds the deterministic findings from BOTH panels (dead code: unreachable
methods, thin handlers, isolated repos; gaps: orphan producers/consumers,
dependency cycles), steers toward cautious recommendations, and degrades
gracefully when the graph predates the reachability phase.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from engine.context_builder import ContextBuilder


def _graph() -> dict:
    """A tiny graph with one thin handler and one unreachable method."""
    return {
        "repos": ["order-service", "fulfillment-service"],
        "cross_repo_links": [{
            "channel": "order-events",
            "producers": ["order-service"],
            "consumers": ["fulfillment-service"],
        }],
        "entry_points": [{
            "id": "ep1", "repo": "order-service", "type": "rest-endpoint",
            "channel": "GET /orders", "method": "createOrder",
            "file": "orders/OrderController.java", "line": 12,
            "metrics": {"total_nodes": 1, "thin": True},
        }],
        "unreachable_methods": [{
            "id": "order-service:Order.validate", "repo": "order-service",
            "class_name": "Order", "method": "validate",
            "file": "orders/Order.java", "line": 26,
        }],
        "methods_total": 50,
    }


def test_dead_code_prompt_embeds_findings():
    p = ContextBuilder(_graph()).build_dead_code_prompt()
    assert "DEAD CODE ANALYSIS" in p
    assert "1 of 50 methods are unreachable" in p
    assert "order-service → validate (orders/Order.java:26)" in p
    assert "Thin handlers" in p
    # The static-analysis caveat must be present so the AI doesn't promise
    # guaranteed-safe deletions.
    assert "*static*" in p or "static" in p


def test_dead_code_prompt_without_method_index():
    """Old graphs lack unreachable_methods — the prompt should say to rescan."""
    g = _graph()
    del g["unreachable_methods"]
    p = ContextBuilder(g).build_dead_code_prompt()
    assert "predates the reachability phase" in p
    assert "rescan" in p


def test_dead_code_prompt_isolated_repos():
    g = _graph()
    # Remove the only cross-repo link -> both repos become isolated.
    g["cross_repo_links"] = []
    p = ContextBuilder(g).build_dead_code_prompt()
    assert "order-service, fulfillment-service" in p


def test_dead_code_prompt_embeds_gaps():
    """The Code Issues view has a Gaps panel too — the prompt must embed
    orphan producers/consumers, not just dead-code findings."""
    g = _graph()
    g["producers"] = [
        {"id": "order-service:OrderController.publish", "repo": "order-service",
         "type": "kafka-producer", "channel": "order-events", "method": "publish",
         "file": "orders/OrderController.java", "line": 30},
        {"id": "order-service:Metrics.emit", "repo": "order-service",
         "type": "kafka-producer", "channel": "metrics-jobs", "method": "emit",
         "file": "orders/Metrics.java", "line": 8},
    ]
    g["entry_points"].append({
        "id": "ep2", "repo": "order-service", "type": "kafka-consumer",
        "channel": "order-events", "method": "onOrderEvent",
        "file": "orders/OrderListener.java", "line": 9,
        "metrics": {"total_nodes": 3},
    })
    g["entry_points"].append({
        "id": "ep3", "repo": "notification-service", "type": "kafka-consumer",
        "channel": "audit-events", "method": "onAudit",
        "file": "notify/AuditListener.java", "line": 5,
        "metrics": {"total_nodes": 3},
    })
    p = ContextBuilder(g).build_dead_code_prompt()
    assert "GAPS ANALYSIS" in p
    # metrics-jobs is produced but never consumed; audit-events consumed but
    # never produced; order-events has both sides and must NOT be flagged.
    assert "ORPHAN PRODUCER metrics-jobs" in p
    assert "ORPHAN CONSUMER audit-events" in p
    assert "ORPHAN PRODUCER order-events" not in p


def test_dead_code_prompt_embeds_cycles():
    g = _graph()
    g["repos"] = ["order-service", "fulfillment-service"]
    g["cross_repo_links"] = [
        {"channel": "order-events", "producers": ["order-service:OrderController.publish"],
         "consumers": ["fulfillment-service:ShipConsumer.handle"], "kind": "message"},
        {"channel": "shipment-events", "producers": ["fulfillment-service:ShipSvc.emit"],
         "consumers": ["order-service:OrderListener.onOrderEvent"], "kind": "message"},
    ]
    p = ContextBuilder(g).build_dead_code_prompt()
    assert "Dependency cycles:" in p
    # Cycles are anchored at their alphabetically-lowest member, so this one
    # reads fulfillment-service → order-service → fulfillment-service.
    assert "CYCLE fulfillment-service → order-service → fulfillment-service" in p
    assert "via order-events, shipment-events" in p


def test_dead_code_prompt_clean_system():
    """No findings anywhere -> the prompt says 'none' rather than crashing."""
    g = _graph()
    g["producers"] = [{
        "id": "order-service:OrderController.publish", "repo": "order-service",
        "type": "kafka-producer", "channel": "order-events", "method": "publish",
        "file": "orders/OrderController.java", "line": 30,
    }]
    g["entry_points"].append({
        "id": "ep2", "repo": "fulfillment-service", "type": "kafka-consumer",
        "channel": "order-events", "method": "onOrderEvent",
        "file": "ship/OrderListener.java", "line": 9,
        "metrics": {"total_nodes": 3},
    })
    g["unreachable_methods"] = []
    p = ContextBuilder(g).build_dead_code_prompt()
    assert "Orphan channels: none" in p
    assert "Dependency cycles: none." in p
