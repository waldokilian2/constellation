"""BoardProvider interface — the contract every board source implements.

Pure data in/out; transport (MCP / REST) is the provider's concern. Mirrors
the extension discipline of ``engine/languages`` (add a provider without
editing the core) and the error style of ``engine/git_hosts.GitHostError``.
"""
from __future__ import annotations

from engine.models import Board, BoardItem


class BoardError(Exception):
    """Raised for provider failures. ``status`` carries an HTTP-style code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class BoardProvider:
    """Read/write an external board as normalized :class:`BoardItem` values.

    A *board* is a dict shaped like :class:`engine.models.Board` (at minimum
    ``provider`` + provider-specific ``config``). Implementations translate
    each method into their transport's vocabulary (MCP tool calls, REST, …).
    """

    name: str = "base"

    # ── read ───────────────────────────────────────────────────────

    def list_boards(self) -> list[dict]:
        """Boards available at this source (``{id, name, kind, source_url}``)."""
        raise NotImplementedError

    def list_items(self, board: dict) -> list[BoardItem]:
        """All items on a board (open + closed unless the board scopes it)."""
        raise NotImplementedError

    def get_item(self, board: dict, item_id: str) -> BoardItem | None:
        """One item by provider id, or ``None`` if absent."""
        raise NotImplementedError

    # ── write (Phase 2) ────────────────────────────────────────────

    def update_item(self, board: dict, item_id: str, patch: dict) -> BoardItem:
        """Apply ``patch`` (``{status/title/labels/...}``) and return the new state."""
        raise NotImplementedError

    def add_comment(self, board: dict, item_id: str, body: str) -> dict:
        """Add a comment; return a small status dict."""
        raise NotImplementedError
