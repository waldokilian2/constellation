"""Event-loop policy setup — cross-platform decision + install wiring.

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).

The policy *decision* is pure and platform-parametric, so it is fully tested on
every OS in CI. The actual policy swap only happens on Windows and can't be
exercised on Linux, so we assert the no-op behaviour there.
"""
from __future__ import annotations
import asyncio
import sys

import win_accept_resilience as war


# ── policy decision (pure, every platform) ────────────────────────

def test_selector_kind_on_windows():
    # Only "win32" is Windows in CPython's sys.platform; cygwin is unix-like and
    # already uses the selector loop, so it must NOT be remapped.
    assert war.desired_policy_kind("win32") == "selector"
    assert war.desired_policy_kind("cygwin") is None


def test_no_kind_off_windows():
    for plat in ("linux", "darwin", "freebsd13"):
        assert war.desired_policy_kind(plat) is None


def test_target_kind_matches_running_platform():
    # Module-level constant must agree with the pure helper for this OS.
    assert war.TARGET_POLICY_KIND == war.desired_policy_kind()


# ── install wiring ────────────────────────────────────────────────

def test_install_returns_bool():
    assert isinstance(war.install(), bool)


def test_install_is_noop_off_windows():
    if sys.platform.startswith("win"):
        return  # Windows installs a real policy; covered implicitly
    before = asyncio.get_event_loop_policy()
    assert war.install() is False
    # Policy must be untouched on non-Windows.
    assert asyncio.get_event_loop_policy() is before
