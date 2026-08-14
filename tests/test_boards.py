"""
Tests for the boards feature (engine/boards + project_store persistence).

Stdlib-only — repo convention (no pytest). The MCP provider is exercised with
an injected *fake session* so no network or Docker is required.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path

from engine.boards import provider_for, BoardError
from engine.boards.mcp_provider import (
    McpBoardProvider,
    _number_from_id,
    _result_json,
)
from engine.project_store import ProjectStore


# ── fake MCP session ────────────────────────────────────────────────────


class _Block:
    def __init__(self, text: str):
        self.text = text


class _Result:
    """Mimics mcp CallToolResult: .content (blocks with .text) + .isError."""

    def __init__(self, payload, is_error: bool = False):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = [_Block(text)] if payload is not None else []
        self.isError = is_error


class _FakeSession:
    """A scripted MCP session. ``responses`` maps tool -> payload | callable."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []  # (tool, args) per call_tool

    async def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        handler = self.responses.get(name)
        if callable(handler):
            return handler(args)
        if isinstance(handler, Exception):
            raise handler
        if handler is None:
            raise BoardError(f"unexpected tool call {name}", status=500)
        return handler


def _factory(session):
    """Return a zero-arg callable yielding an async CM that yields `session`."""

    @contextlib.asynccontextmanager
    async def factory():
        yield session

    return factory


def _provider(board, session):
    return provider_for(board, session_factory=_factory(session))


BOARD = {"provider": "github-mcp", "config": {"owner": "waldokilian2", "repo": "constellation"}}


def test_number_from_id_roundtrip():
    assert _number_from_id("github:waldokilian2/constellation#49") == 49
    try:
        _number_from_id("not-a-number")
    except BoardError:
        pass
    else:
        raise AssertionError("expected BoardError for malformed id")


def test_result_json_raises_on_error():
    class R:
        isError = True
        content = [_Block("boom")]

    try:
        _result_json(R())
    except BoardError:
        return
    raise AssertionError("expected BoardError on isError result")


def test_list_items_parses_issues_and_filters_prs():
    issues = {
        "items": [
            {"number": 49, "title": "Boards page", "state": "OPEN",
             "labels": [{"name": "enhancement"}],
             "html_url": "https://github.com/waldokilian2/constellation/issues/49"},
            {"number": 48, "title": "Multi-lang", "state": "OPEN", "assignee": {"login": "alice"}},
            {"number": 47, "title": "[PR] somerepo", "state": "OPEN", "pull_request": {"url": "x"}},
        ],
        "pageInfo": {"hasNextPage": False},
    }
    session = _FakeSession({"list_issues": _Result(issues)})
    items = asyncio.run(_provider(BOARD, session).list_items())

    # PR filtered out; 2 issues remain; ids/numbers/status normalized.
    nums = sorted(i.number for i in items)
    assert nums == ["48", "49"], nums
    by_num = {i.number: i for i in items}
    assert by_num["49"].status == "open"  # OPEN -> lowercase
    assert by_num["49"].labels == ["enhancement"]
    assert by_num["48"].assignee == "alice"
    assert by_num["49"].id == "github:waldokilian2/constellation#49"
    assert by_num["49"].url  # something truthy


def test_list_items_paginates_via_cursor():
    def responder(args):
        if args.get("after") is None:
            return _Result({
                "items": [{"number": 1, "title": "a", "state": "open"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "CUR1"},
            })
        assert args["after"] == "CUR1", args
        return _Result({
            "items": [{"number": 2, "title": "b", "state": "closed"}],
            "pageInfo": {"hasNextPage": False},
        })

    session = _FakeSession({"list_issues": responder})
    items = asyncio.run(_provider(BOARD, session).list_items())
    assert sorted(i.number for i in items) == ["1", "2"]
    assert len(session.calls) == 2  # exactly two pages


def test_project_list_items_maps_status_field():
    """GitHub Project items map the Status field to BoardItem.status (column)."""
    items_payload = {
        "items": [
            {
                "id": 227392682,
                "content_type": "Issue",
                "content": {
                    "number": 13,
                    "title": "Graph diff &amp; versioning",
                    "state": "open",
                    "html_url": "https://github.com/waldokilian2/constellation/issues/13",
                    "assignees": ["Malbogle"],
                    "labels": ["enhancement", "area/engine"],
                    "repository": "waldokilian2/constellation",
                },
                "fields": [
                    {"id": 378912731, "name": "Status", "data_type": "single_select",
                     "value": {"id": "f75ad846", "name": "Backlog", "color": "GREEN"}},
                ],
                "updated_at": "2026-08-12T07:34:30Z",
            },
            {
                "id": 227392700,
                "content_type": "Issue",
                "content": {
                    "number": 30,
                    "title": "Runtime trace overlay",
                    "html_url": "https://github.com/waldokilian2/constellation/issues/30",
                },
                "fields": [
                    {"id": 378912731, "name": "Status", "data_type": "single_select",
                     "value": {"id": "abc", "name": "In Progress", "color": "BLUE"}},
                ],
                "updated_at": "2026-08-12T08:00:00Z",
            },
        ],
        "pageInfo": {"hasNextPage": False},
    }
    board = {"provider": "github-mcp", "config": {"owner": "waldokilian2", "project_number": 2}}
    session = _FakeSession({"projects_list": _Result(items_payload)})
    items = asyncio.run(_provider(board, session).list_items())

    assert len(items) == 2, items
    by_num = {i.number: i for i in items}
    assert by_num["13"].status == "Backlog"
    assert by_num["13"].assignee == "Malbogle"
    assert by_num["13"].labels == ["enhancement", "area/engine"]
    assert by_num["13"].title == "Graph diff & versioning"  # html.unescape applied
    assert by_num["30"].status == "In Progress"
    assert by_num["13"].url.startswith("https://github.com/")
    # item id encodes the project-item id so write-back can address it
    assert by_num["13"].id.endswith("#227392682")
    # correct MCP tool + args were used
    name, args = session.calls[0]
    assert name == "projects_list"
    assert args["method"] == "list_project_items"
    assert args["project_number"] == 2
    assert args["field_names"] == ["Status"]


def test_project_update_moves_status_via_projects_write():
    captured = {}

    def write_handler(args):
        captured.update(args)
        return _Result({"id": 227392682})

    board = {"provider": "github-mcp", "config": {"owner": "waldokilian2", "project_number": 2}}
    session = _FakeSession({"projects_write": write_handler, "projects_get": _Result({
        "id": 227392682,
        "content": {"number": 13, "title": "Graph diff", "html_url": "https://github.com/x/issues/13"},
        "fields": [{"name": "Status", "value": {"name": "In Progress"}}],
    })})
    p = _provider(board, session)
    out = asyncio.run(p.update_item(board, "github-project:waldokilian2/2#227392682", {"status": "In Progress"}))

    assert captured["method"] == "update_project_item"
    assert captured["project_number"] == 2
    assert captured["item_id"] == 227392682
    assert captured["updated_field"] == {"name": "Status", "value": "In Progress"}
    assert out.status == "In Progress"


def test_update_item_normalizes_state_lowercase():
    captured = {}

    def write_handler(args):
        captured.update(args)
        return _Result({"number": 49, "title": "Boards page", "state": "closed", "labels": []})

    session = _FakeSession({"issue_write": write_handler})
    p = _provider(BOARD, session)
    out = asyncio.run(p.update_item(BOARD, "github:waldokilian2/constellation#49", {"status": "CLOSED"}))

    # issue_write method=update; state is lowercase regardless of input case
    assert captured["method"] == "update"
    assert captured["issue_number"] == 49
    assert captured["state"] == "closed", captured
    assert out.status == "closed"


def test_add_comment_calls_correct_tool():
    session = _FakeSession({"add_issue_comment": _Result({"ok": True})})
    res = asyncio.run(_provider(BOARD, session).add_comment(BOARD, "github:o/r#12", "hi"))
    assert res["ok"] is True
    name, args = session.calls[0]
    assert name == "add_issue_comment"
    assert args == {"owner": "waldokilian2", "repo": "constellation", "issue_number": 12, "body": "hi"}


def test_board_tools_list_boards_and_items():
    """AI chat board tools: list boards + cached items (no provider call)."""
    from engine.boards.tools import execute_board_tool

    boards = [{
        "id": "github-project:waldokilian2/2", "provider": "github-mcp", "name": "Constellation Board", "kind": "project",
        "items": [
            {"id": "g#1", "number": "13", "title": "Graph diff", "status": "Backlog", "labels": ["enhancement"]},
            {"id": "g#2", "number": "15", "title": "Blast radius", "status": "In progress", "labels": []},
        ],
    }]

    class FakeStore:
        def __init__(self):
            self._boards = boards
        def load_boards(self, pid):
            return {"boards": self._boards}
        def save_boards(self, pid, data):
            self._boards = data["boards"]

    store = FakeStore()
    res = execute_board_tool(store, "proj-1", "list_boards", {})
    assert res["boards"][0]["name"] == "Constellation Board"
    assert res["boards"][0]["items"] == 2
    assert res["boards"][0]["columns"] == {"Backlog": 1, "In progress": 1}

    res = execute_board_tool(store, "proj-1", "list_board_items", {"status": "Backlog"})
    assert [i["number"] for i in res["items"]] == ["13"]

    res = execute_board_tool(store, "proj-1", "list_board_items", {})
    assert len(res["items"]) == 2


def test_board_tools_move_persists_and_calls_provider():
    """move_board_item calls the provider's update_item and persists the cache."""
    from unittest.mock import patch
    from engine.boards.tools import execute_board_tool
    from engine.models import BoardItem

    boards = [{
        "id": "github-project:waldokilian2/2", "provider": "github-mcp", "name": "Constellation Board", "kind": "project",
        "items": [{"id": "github-project:waldokilian2/2#111", "number": "13", "title": "Graph diff", "status": "Backlog"}],
    }]

    class FakeStore:
        def __init__(self):
            self._boards = boards
        def load_boards(self, pid):
            return {"boards": self._boards}
        def save_boards(self, pid, data):
            self._boards = data["boards"]

    class FakeProvider:
        def __init__(self):
            self.moves = []
        async def update_item(self, board, item_id, patch):
            self.moves.append((item_id, patch))
            return BoardItem(id=item_id, number="13", title="Graph diff", status=patch["status"])
        async def add_comment(self, board, item_id, body):
            return {"ok": True}

    fp = FakeProvider()
    with patch("engine.boards.tools.provider_for", return_value=fp):
        res = execute_board_tool(FakeStore(), "proj-1", "move_board_item",
                                 {"item_id": "github-project:waldokilian2/2#111", "status": "In progress"})
    assert res["ok"] is True
    assert fp.moves == [("github-project:waldokilian2/2#111", {"status": "In progress"})]


def test_board_tools_no_boards_returns_clear_error():
    from engine.boards.tools import execute_board_tool

    class EmptyStore:
        def load_boards(self, pid):
            return {"boards": []}

    res = execute_board_tool(EmptyStore(), "proj-1", "move_board_item", {"item_id": "x", "status": "Done"})
    assert "No boards are connected" in res["error"]


def test_create_item_project_board_creates_issue_and_adds():
    """create_item: duplicate search → issue_write create → add_project_item → lane by issue-ref."""
    calls = []

    def issue_write(args):
        calls.append(("issue_write", args))
        return _Result({"number": 99, "title": args.get("title"), "state": "open",
                        "html_url": "https://github.com/waldokilian2/constellation/issues/99"})

    def projects_write(args):
        calls.append(("projects_write", args))
        return _Result({"ok": True})

    def projects_list(args):
        calls.append(("projects_list", args))
        if args.get("method") == "list_project_items":
            return _Result({"items": [{
                "id": 555001,
                "content": {"number": 99, "title": "New thing", "state": "open",
                            "html_url": "https://github.com/waldokilian2/constellation/issues/99"},
                "fields": [{"name": "Status", "value": {"name": "Backlog"}}],
            }], "pageInfo": {"hasNextPage": False}})
        return _Result({"projects": []})

    board = {"provider": "github-mcp", "kind": "project",
             "config": {"owner": "waldokilian2", "project_number": 2},
             # the repo is learned from an existing item's raw content
             "items": [{"id": "x#1", "raw": {"content": {"repository": "waldokilian2/constellation"}}}]}
    def search_issues(args):
        calls.append(("search_issues", args))
        return _Result({"items": []})  # no duplicate

    session = _FakeSession({
        "search_issues": search_issues,
        "issue_write": issue_write,
        "projects_write": projects_write,
        "projects_list": projects_list,
    })
    item = asyncio.run(_provider(board, session).create_item(board, "New thing", body="desc", labels=["bug"], status="Backlog"))

    tools = [(n, a) for (n, a) in calls]
    # 1) duplicate guard searched first
    assert tools[0][0] == "search_issues"
    # 2) issue created in the repo derived from existing items
    create = next(a for n, a in tools if n == "issue_write")
    assert create["method"] == "create" and create["repo"] == "constellation"
    assert create["labels"] == ["bug"]
    # 3) added to the project
    assert any(n == "projects_write" and a["method"] == "add_project_item" and a["issue_number"] == 99
               for n, a in tools)
    # 4) lane set by ISSUE REFERENCE (works with any add-response id format)
    assert any(n == "projects_write" and a["method"] == "update_project_item"
               and a.get("issue_number") == 99
               and a["updated_field"] == {"name": "Status", "value": "Backlog"}
               for n, a in tools)
    # returns the canonical project item
    assert item.number == "99" and item.status == "Backlog"


def test_create_item_duplicate_title_returns_existing():
    """The duplicate guard: an open issue with the same title is returned, not re-created."""
    session = _FakeSession({
        "search_issues": _Result({"items": [
            {"number": 81, "title": "Add retry logic", "state": "open",
             "html_url": "https://github.com/waldokilian2/constellation/issues/81"},
        ]}),
    })
    board = {"provider": "github-mcp", "kind": "issues",
             "config": {"owner": "waldokilian2", "repo": "constellation"},
             "items": []}
    item = asyncio.run(_provider(board, session).create_item(board, "Add retry logic"))
    # returned the existing issue; issue_write (create) was never called
    assert item.number == "81"
    assert all(n != "issue_write" for n, _ in session.calls)


def test_create_item_closed_duplicate_does_not_block():
    """A CLOSED issue with the same title is not a duplicate — a new one is created
    (and its status must not be the closed state, which would add a 'closed' lane)."""
    calls = []

    def issue_write(args):
        calls.append(args)
        return _Result({"number": 90, "title": args.get("title"), "state": "open",
                        "html_url": "https://github.com/waldokilian2/constellation/issues/90"})

    session = _FakeSession({
        "search_issues": _Result({"items": [
            {"number": 81, "title": "Add retry logic", "state": "closed",
             "html_url": "https://github.com/waldokilian2/constellation/issues/81"},
        ]}),
        "issue_write": issue_write,
    })
    board = {"provider": "github-mcp", "kind": "issues",
             "config": {"owner": "waldokilian2", "repo": "constellation"},
             "items": []}
    item = asyncio.run(_provider(board, session).create_item(board, "Add retry logic"))
    # a new open issue was created, NOT the closed one
    assert item.number == "90"
    assert len(calls) == 1  # exactly one create


def test_board_tools_create_item_caches_result():
    from unittest.mock import patch
    from engine.boards.tools import execute_board_tool
    from engine.models import BoardItem

    boards = [{
        "id": "github-project:waldokilian2/2", "provider": "github-mcp", "kind": "project",
        "name": "Constellation Board",
        "items": [],
    }]
    state = {"boards": boards}

    class Store:
        def load_boards(self, pid): return state
        def save_boards(self, pid, d): state["boards"] = d["boards"]

    class FakeProvider:
        async def create_item(self, board, title, body, labels, status):
            return BoardItem(id="github-project:waldokilian2/2#777", number="100",
                             title=title, status=status or "Backlog")

    with patch("engine.boards.tools.provider_for", return_value=FakeProvider()):
        res = execute_board_tool(Store(), "p", "create_board_item",
                                 {"title": "AI-made issue", "status": "Backlog"})
    assert res["ok"] is True and res["item"]["number"] == "100"
    # cached onto the board
    assert state["boards"][0]["items"][0]["id"].endswith("#777")


def test_provider_for_unknown_raises():
    try:
        provider_for({"provider": "nope"})
    except BoardError as e:
        assert e.status == 400
    else:
        raise AssertionError("expected BoardError for unknown provider")


def test_project_store_boards_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        store = ProjectStore(Path(d))
        meta = store.create_meta("Demo")
        pid = meta["id"]

        # Absent -> empty structure, never raises
        assert store.load_boards(pid) == {"boards": []}

        data = {"boards": [{"id": "github:o/r", "provider": "github-mcp", "items": []}]}
        store.save_boards(pid, data)
        assert store.load_boards(pid) == data
        assert store.boards_path(pid).name == "boards.json"


def test_project_store_load_boards_resists_corrupt_file():
    with tempfile.TemporaryDirectory() as d:
        store = ProjectStore(Path(d))
        pid = store.create_meta("Demo")["id"]
        store.save_boards(pid, {"boards": []})
        # Corrupt the file
        store.boards_path(pid).write_text("{not json")
        assert store.load_boards(pid) == {"boards": []}
