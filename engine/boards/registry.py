"""Resolve a :class:`BoardProvider` for a connected board.

Adding a provider never touches this module beyond the dispatch below — the
same discipline as ``engine/languages/registry.py``. Imports are lazy so an
optional provider's deps (e.g. the ``mcp`` SDK) are only loaded when used.
"""
from __future__ import annotations

from .base import BoardProvider, BoardError


def provider_for(board: dict, **kwargs) -> BoardProvider:
    """Instantiate the provider named in ``board["provider"]``."""
    provider = (board or {}).get("provider") or ""
    if provider == "github-mcp":
        from .mcp_provider import McpBoardProvider
        return McpBoardProvider(board=board, **kwargs)
    raise BoardError(f"Unknown board provider: {provider!r}", status=400)


def available_providers() -> list[str]:
    """Provider tags currently wired up (for UI dropdowns / docs)."""
    return ["github-mcp"]
