"""Tests for engine/context_builder.py — page-scoped system prompts.

Run with: python tests/run_tests.py test_context_builder

These are stdlib-only and cover the Code Issues (dead-code) system prompt:
that it embeds the deterministic findings (unreachable methods, thin
handlers, isolated repos), steers toward cautious recommendations, and
degrades gracefully when the graph predates the reachability phase.
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
