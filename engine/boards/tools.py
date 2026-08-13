"""Board tools for the AI chat tool-use loop.

These let the assistant read and update the project's synced boards — list
boards/items, move a card's status, comment — through the same MCP provider the
UI uses. The provider is async (the MCP client is async); the server runs these
with ``_run_async`` (a fresh thread + event loop) from the blocking tool loop.
"""
from __future__ import annotations

import asyncio
import threading

from .base import BoardError
from .registry import provider_for

BOARD_TOOL_DEFINITIONS = [
    {
        "name": "list_boards",
        "description": (
            "List the boards connected to this project (name, kind, item count, "
            "column breakdown)."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_board_items",
        "description": (
            "List items on a board (number, title, status, assignee, labels). "
            "Optionally filter by status column (e.g. 'Backlog', 'In progress'). "
            "Uses the last synced state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Board id (defaults to the first connected board)."},
                "status": {"type": "string", "description": "Optional status/column to filter by."},
            },
            "required": [],
        },
    },
    {
        "name": "move_board_item",
        "description": (
            "Move a board item to a different status column (e.g. from 'In progress' "
            "to 'In review'). Writes the change to GitHub immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "The board item id (e.g. 'github-project:owner/2#227392719'). Use list_board_items to find it."},
                "status": {"type": "string", "description": "Target status column name (e.g. 'Backlog', 'In progress', 'In review', 'Done')."},
                "board_id": {"type": "string", "description": "Optional board id."},
            },
            "required": ["item_id", "status"],
        },
    },
    {
        "name": "add_board_comment",
        "description": "Add a comment to a board item's underlying GitHub issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "The board item id."},
                "body": {"type": "string", "description": "Comment text."},
                "board_id": {"type": "string", "description": "Optional board id."},
            },
            "required": ["item_id", "body"],
        },
    },
]

BOARD_TOOL_NAMES = {t["name"] for t in BOARD_TOOL_DEFINITIONS}


def _run_async(factory):
    """Run an async coroutine in a fresh thread + event loop.

    ``_stream_llm_events`` runs on the main event loop in the non-streaming
    path, so ``asyncio.run`` directly would raise — spawning a dedicated thread
    is safe in both cases.
    """
    result: dict = {}

    def runner():
        result["val"] = asyncio.run(factory())

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    return result["val"]


def _upsert_item(board: dict, item: dict) -> None:
    items = board.get("items") or []
    for i, it in enumerate(items):
        if it.get("id") == item.get("id"):
            items[i] = item
            return
    items.append(item)
    board["items"] = items


def _column_summary(board: dict) -> dict:
    counts: dict[str, int] = {}
    for it in board.get("items", []):
        s = it.get("status") or "no status"
        counts[s] = counts.get(s, 0) + 1
    return counts


async def _execute(store, pid: str, tool_name: str, args: dict) -> dict:
    """Core async dispatch — used by the thread runner."""
    boards = store.load_boards(pid).get("boards", [])
    if not boards:
        return {"error": "No boards are connected for this project. Connect one in the Boards view first."}

    def _board(bid: str | None) -> dict:
        if not bid:
            return boards[0]
        for b in boards:
            if b["id"] == bid:
                return b
        raise BoardError(f"Board '{bid}' not found. Available: {[b['id'] for b in boards]}", status=404)

    if tool_name == "list_boards":
        return {
            "boards": [
                {
                    "id": b["id"], "name": b.get("name"), "kind": b.get("kind"),
                    "items": len(b.get("items", [])), "columns": _column_summary(b),
                }
                for b in boards
            ]
        }

    board = _board(args.get("board_id"))

    if tool_name == "list_board_items":
        # Reads the last-synced cache — no provider/network call needed.
        items = list(board.get("items", []))
        status = args.get("status")
        if status:
            items = [it for it in items if (it.get("status") or "").lower() == status.lower()]
        return {"items": items[:50], "board": board.get("name", board.get("id"))}

    provider = provider_for(board)  # only writes need a live provider

    if tool_name == "move_board_item":
        item = await provider.update_item(board, args["item_id"], {"status": args["status"]})
        # Keep the persisted cache consistent so later list/sync show the move.
        data = store.load_boards(pid)
        for b in data.get("boards", []):
            if b["id"] == board["id"]:
                _upsert_item(b, item.to_dict())
        store.save_boards(pid, data)
        return {
            "ok": True, "item": item.to_dict(),
            "message": f"Moved {args['item_id']} to '{args['status']}'.",
        }

    if tool_name == "add_board_comment":
        await provider.add_comment(board, args["item_id"], args["body"])
        return {"ok": True, "item_id": args["item_id"], "message": "Comment added."}

    return {"error": f"Unknown board tool: {tool_name}"}


def execute_board_tool(store, pid: str, tool_name: str, args: dict) -> dict:
    """Run a board tool call from the (blocking) chat tool loop."""
    try:
        return _run_async(lambda: _execute(store, pid, tool_name, args))
    except BoardError as e:
        return {"error": str(e)}
    except Exception as e:  # transport / provider failures
        return {"error": f"Board tool failed: {e}"}
