"""Mermaid validator regression checks.

Run with: python tests/run_tests.py
Stdlib only. The validator shells out to Node + the bundled mermaid package; if
that toolchain is missing the tests pass trivially (the validator degrades to
"accept"), otherwise they assert real parse verdicts.

Validation runs on the REPAIRED source (same repair the browser applies), so a
diagram the browser would render is accepted and the repaired code is
returned; only genuinely broken syntax is rejected.
"""
from __future__ import annotations
from engine.mermaid_validator import is_available, validate_mermaid

HAVE = is_available()


def _if_have():
    if not HAVE:
        import sys
        print("      (skipped: node/mermaid validator unavailable)")
        sys.exit(0)


def test_empty_is_invalid():
    _if_have()
    ok, err, _ = validate_mermaid("")
    assert ok is False
    assert err


def test_quoted_node_id_is_repaired_not_rejected():
    # A quoted path used as a node id with a trailing bracket is fixed by the
    # shared repair (auto_1["..."]) — accepted with the repaired code, so the
    # AI isn't sent into a retry loop over a fixable mistake.
    _if_have()
    ok, _, code = validate_mermaid('flowchart LR\nA -.->|REST| "/api/x/{id}"[http]')
    assert ok is True
    assert 'auto_1["/api/x/{id}"]' in code


def test_valid_flowchart_passes_and_returns_repaired_code():
    _if_have()
    ok, _, code = validate_mermaid("flowchart LR\nA[Order] --> B[Pay]")
    assert ok is True
    assert "A[Order]" in code


def test_bare_label_is_repaired_not_rejected():
    # A bare edge label with braces/slashes would fail raw, but the shared
    # repair quotes it — so it must be accepted (this previously caused the
    # AI to loop on fixable trivia).
    _if_have()
    ok, _, code = validate_mermaid(
        "flowchart LR\nA -.->|GET /api/x/{id}| B"
    )
    assert ok is True
    assert '|"GET /api/x/{id}"|' in code


def test_valid_sequence_passes():
    _if_have()
    ok, _, _ = validate_mermaid("sequenceDiagram\nAlice->>Bob: Hi")
    assert ok is True


def test_unclosed_bracket_is_invalid():
    _if_have()
    ok, _, _ = validate_mermaid("flowchart LR\nA[Order")
    assert ok is False


def test_result_shape():
    ok, err, code = validate_mermaid("flowchart LR\nA --> B")
    assert isinstance(ok, bool)
    assert isinstance(err, str)
    assert isinstance(code, str)
