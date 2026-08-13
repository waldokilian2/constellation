"""External board sync (GitHub via the official GitHub MCP server).

A board — a Jira project, GitHub Issues, or GitHub Projects board — is synced
into Constellation through a :class:`BoardProvider`. The MCP provider talks to
the official GitHub MCP server (``github/github-mcp-server``); a Jira provider
can sit behind the same interface later. Board data is persisted per project
in ``boards.json`` (see :class:`engine.project_store.ProjectStore`).
"""
from .base import BoardProvider, BoardError  # noqa: F401
from .registry import provider_for, available_providers  # noqa: F401
