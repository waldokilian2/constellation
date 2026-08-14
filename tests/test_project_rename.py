"""
Tests for the project rename feature (ProjectStore.rename + PATCH semantics).

Stdlib-only — repo convention (no pytest). Renames are metadata-only: the id,
graph, repos and on-disk layout never change, so the tests assert that too.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from engine.project_store import ProjectStore


def _fresh_store():
    """A ProjectStore backed by a throwaway directory."""
    tmp = tempfile.TemporaryDirectory()
    return ProjectStore(Path(tmp.name)), tmp


def test_rename_updates_name_and_persists():
    store, tmp = _fresh_store()
    try:
        meta = store.create_meta("Old Name")
        pid = meta["id"]

        updated = store.rename(pid, "New Name")
        assert updated is not None
        assert updated["name"] == "New Name"
        assert updated["id"] == pid

        # Persisted to the index on disk.
        data = json.loads(store.index_file.read_text())
        assert [p["name"] for p in data] == ["New Name"]
        assert store.list_projects()[0]["name"] == "New Name"
    finally:
        tmp.cleanup()


def test_rename_keeps_id_and_layout():
    store, tmp = _fresh_store()
    try:
        meta = store.create_meta("Original")
        pid = meta["id"]
        pdir = store.project_dir(pid)

        # A repo-less project still has a dir; rename must not touch it.
        store.rename(pid, "Renamed")
        assert store.project_dir(pid) == pdir
        assert pdir.exists()
        assert store.get_project(pid)["id"] == pid
    finally:
        tmp.cleanup()


def test_rename_unknown_project_returns_none():
    store, tmp = _fresh_store()
    try:
        assert store.rename("does-not-exist", "Whatever") is None
    finally:
        tmp.cleanup()


def test_rename_blank_name_raises():
    store, tmp = _fresh_store()
    try:
        pid = store.create_meta("Demo")["id"]
        for blank in ("", "   ", None):
            try:
                store.rename(pid, blank)
                raise AssertionError(f"expected ValueError for {blank!r}")
            except ValueError:
                pass
        # Original name is untouched after the failed attempts.
        assert store.get_project(pid)["name"] == "Demo"
    finally:
        tmp.cleanup()


def test_rename_strips_whitespace():
    store, tmp = _fresh_store()
    try:
        pid = store.create_meta("Demo")["id"]
        updated = store.rename(pid, "  Trimmed  ")
        assert updated["name"] == "Trimmed"
    finally:
        tmp.cleanup()
