"""GitHub board provider over the official GitHub MCP server.

Constellation is an **MCP client** to ``github/github-mcp-server``. That server
already exposes the tools we need for both directions and both sources:

  * issues  — ``list_issues`` / ``issue_read`` / ``issue_write`` / ``add_issue_comment``
  * project — ``projects_list`` / ``projects_get`` / ``projects_write``

We use the **remote hosted server** directly (no self-hosted Docker needed):
``https://api.githubcopilot.com/mcp/``. Projects v2 lives in a non-default
toolset, so the session sends ``X-MCP-Toolsets: issues,projects`` (or you can
connect to the projects-only URL ``https://api.githubcopilot.com/mcp/x/projects``
via ``GITHUB_MCP_URL``). Stdio (local Docker/Go binary) is selectable via
``GITHUB_MCP_TRANSPORT``.

Auth: a GitHub PAT. Resolved from ``GITHUB_PERSONAL_ACCESS_TOKEN`` (alias
``GITHUB_TOKEN``), falling back to ``gh auth token`` for local dev. The token
never reaches the browser — only the server talks to the MCP server.

This module is async (the ``mcp`` SDK is async). The FastAPI handlers await it
directly. Tests inject a fake ``session_factory`` so no network/Docker is needed.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import subprocess
from typing import Any

from engine.models import Board, BoardItem

from .base import BoardError, BoardProvider

# ── GitHub MCP server tool vocab (verified from __toolsnaps__) ──────────
# list_issues:   {owner, repo, perPage?, state?: "OPEN"|"CLOSED", after?}
#                -> {"items": [...], "pageInfo": {"endCursor", "hasNextPage"}}
# issue_write:   {owner, repo, issue_number, method:"update", state?:"open"|"closed", ...}
# add_issue_comment: {owner, repo, issue_number, body}

_REMOTE_URL = "https://api.githubcopilot.com/mcp/"
_STDIO_ARGS_DEFAULT = (
    "run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server"
)
_MAX_PAGES = 10  # perPage=100 → up to 1000 items per board


def _resolve_token() -> str:
    """PAT from env, else ``gh auth token`` (local-dev fallback). Empty if none."""
    tok = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok.strip()
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _github_token_check(token: str) -> tuple[set, str]:
    """Return ``(scopes, error_message)`` for a GitHub PAT.

    Reads the ``X-OAuth-Scopes`` header (present even on error responses) and
    pulls the API's error body for a detailed message (bad credentials /
    insufficient scope), so a failed connect surfaces *why* instead of a generic
    401. ``error`` is "" when the token is usable.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Constellation/0.1",
        },
    )
    header = ""
    error = ""
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        header = resp.headers.get("X-OAuth-Scopes", "") or ""
    except urllib.error.HTTPError as e:
        header = (e.headers or {}).get("X-OAuth-Scopes", "") or ""
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
            msg = payload.get("message") or ""
        except Exception:
            msg = ""
        if e.code == 401:
            error = "GitHub API rejected the token (HTTP 401). " + (
                msg or "Check GITHUB_PERSONAL_ACCESS_TOKEN is a valid, unexpired PAT."
            )
        elif e.code == 403:
            error = "GitHub API access denied (HTTP 403). " + (
                msg or "The token may be missing the required scopes (repo, project)."
            )
        else:
            error = f"GitHub API returned HTTP {e.code}" + (f": {msg}" if msg else "")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        error = f"Could not reach the GitHub API: {e}"
    scopes = {s.strip() for s in header.split(",") if s.strip()}
    return scopes, error


def _capabilities_for(scopes: set[str], error: str = "") -> dict:
    """Map token scopes → what the boards UI may do (move card, comment)."""
    if error:
        return {"move": False, "comment": False, "scopes": [], "error": error, "unknown": False}
    if not scopes:
        # Fine-grained token or undeterminable — don't block; let the op fail clearly.
        return {"move": True, "comment": True, "scopes": [], "unknown": True}
    return {
        "move": "project" in scopes,
        "comment": ("repo" in scopes) or ("public_repo" in scopes),
        "scopes": sorted(scopes),
        "unknown": False,
    }


# ── MCP session context managers ────────────────────────────────────────


@contextlib.asynccontextmanager
async def _http_session(url: str, headers: dict):
    """Streamable-HTTP session to the remote GitHub MCP server."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx2  # httpx fork the mcp SDK pins (transitive dep, already installed)

    # The MCP server's GraphQL-backed calls (list_project_items/fields) can take
    # longer than the httpx default 5s, especially on a cold connection — the
    # default was causing intermittent httpcore2.ReadTimeout -> 502 on sync.
    # Connect fails fast (remote unreachable -> 10s) while read/write stay
    # generous (120s) so slow GraphQL calls — and slow card moves/comments —
    # complete instead of being cancelled by a short timeout.
    timeout = float(os.environ.get("GITHUB_MCP_TIMEOUT", "120"))
    connect_timeout = float(os.environ.get("GITHUB_MCP_CONNECT_TIMEOUT", "10"))
    httpx_timeout = httpx2.Timeout(connect=connect_timeout, read=timeout, write=timeout, pool=connect_timeout)
    async with httpx2.AsyncClient(headers=headers, timeout=httpx_timeout) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read, write = streams  # TransportStreams = 2-tuple (read, write)
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


@contextlib.asynccontextmanager
async def _stdio_session(command: str, args: list[str], env: dict):
    """Stdio session launching the MCP server locally (Docker / Go binary)."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=command, args=args, env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ── result parsing ──────────────────────────────────────────────────────


def _content_text(result: Any) -> str:
    """Join the text of a ``CallToolResult``'s content blocks."""
    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


def _result_json(result: Any) -> Any:
    """Parse a tool result's text as JSON, raising :class:`BoardError` on errors.

    Valid tool results are JSON. Non-JSON text (or ``isError``) is an error
    message from the server — surface it rather than silently treating it as
    "no items" (that bug produced empty boards instead of the real cause).
    """
    if getattr(result, "isError", False):
        raise BoardError(f"GitHub MCP tool error: {_content_text(result)}", status=502)
    text = _content_text(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise BoardError(f"GitHub MCP returned: {text}", status=502)


def _item_from_issue(scope: str, it: dict) -> BoardItem:
    """Map a GitHub issue dict (MCP payload) to a normalized :class:`BoardItem`."""
    number = it.get("number")
    labels = [
        (lbl.get("name") if isinstance(lbl, dict) else lbl)
        for lbl in (it.get("labels") or [])
    ]
    assignee = it.get("assignee") or {}
    return BoardItem(
        id=f"github:{scope}#{number}",
        number=str(number) if number is not None else "",
        title=html.unescape(it.get("title") or ""),  # MCP server HTML-encodes some chars
        status=(it.get("state") or "open").lower(),  # GitHub states are lowercase
        assignee=(assignee.get("login") if isinstance(assignee, dict) else "") or "",
        labels=[l for l in labels if l],
        url=it.get("url") or it.get("html_url") or "",
        updated_at=it.get("updated_at") or "",
        raw=it,
    )


def _number_from_id(item_id: str) -> int:
    """``"github:owner/repo#49"`` → ``49``."""
    try:
        return int(str(item_id).rsplit("#", 1)[-1])
    except (ValueError, IndexError):
        raise BoardError(f"Unrecognized GitHub item id: {item_id!r}", status=400)


def _project_item_id_from(item_id: str) -> int:
    """``"github-project:owner/project#227392682"`` → ``227392682`` (the item id)."""
    return _number_from_id(item_id)


def _status_from_fields(fields: list) -> str:
    """Pull the Status field value out of a project-item's ``fields`` list.

    ``fields`` is ``[{"name": "Status", "value": {"name": "Backlog", ...}}, ...]``.
    """
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        if str(f.get("name") or "").lower() == "status":
            v = f.get("value")
            if isinstance(v, dict):
                return v.get("name") or ""
            if isinstance(v, str):
                return v
    return ""


def _item_from_project(scope: str, it: dict) -> BoardItem:
    """Map a GitHub Projects v2 item (MCP payload) to a normalized BoardItem."""
    content = it.get("content") or {}
    if not isinstance(content, dict):
        content = {}
    number = content.get("number")
    assignees = content.get("assignees") or []
    labels = [
        (lbl.get("name") if isinstance(lbl, dict) else lbl)
        for lbl in (content.get("labels") or [])
    ]
    return BoardItem(
        id=f"github-project:{scope}#{it.get('id')}",
        number=str(number) if number is not None else "",
        title=html.unescape(content.get("title") or ""),
        status=_status_from_fields(it.get("fields") or []),
        assignee=(assignees[0] if assignees else "") or "",
        labels=[l for l in labels if l],
        url=content.get("html_url") or "",
        updated_at=it.get("updated_at") or content.get("updated_at") or "",
        raw=it,
    )


# ── provider ────────────────────────────────────────────────────────────


class McpBoardProvider(BoardProvider):
    """GitHub board source backed by the official GitHub MCP server."""

    name = "github-mcp"

    def __init__(self, board: dict, session_factory=None):
        self.board = board or {}
        cfg = self.board.get("config") or {}
        self.owner = cfg.get("owner") or ""
        self.repo = cfg.get("repo") or ""
        self.project_number = cfg.get("project_number") or cfg.get("project") or 0
        # A github-mcp board is either a repo's *issues* ({owner, repo}) or a
        # user/org *project* v2 board ({owner, project_number}). Default to the
        # kind present in the config; explicit board["kind"] wins.
        if self.board.get("kind") in ("project", "issues"):
            self.kind = self.board["kind"]
        else:
            self.kind = "project" if self.project_number else "issues"
        self._scope = f"{self.owner}/{self.repo or self.project_number or 'projects'}"

        self.transport = os.environ.get("GITHUB_MCP_TRANSPORT", "http").lower()
        self.url = os.environ.get("GITHUB_MCP_URL", _REMOTE_URL)
        self.toolsets = os.environ.get("GITHUB_MCP_TOOLSETS", "issues,projects").strip()
        self.command = os.environ.get("GITHUB_MCP_COMMAND", "docker")
        self.stdio_args = (os.environ.get("GITHUB_MCP_ARGS") or _STDIO_ARGS_DEFAULT).split()

        # session_factory lets tests inject a fake async context manager that
        # yields an object with ``.call_tool(name, args)``.
        self.session_factory = session_factory or self._make_session

    # ── wiring ─────────────────────────────────────────────────────

    def _token(self) -> str:
        tok = _resolve_token()
        if not tok:
            raise BoardError(
                "No GitHub token: set GITHUB_PERSONAL_ACCESS_TOKEN "
                "(or run `gh auth token` works as a fallback).",
                status=401,
            )
        return tok

    def _make_session(self):
        """Build a fresh async-CM session for one tool call."""
        token = self._token()
        if self.transport == "stdio":
            env = dict(os.environ, GITHUB_PERSONAL_ACCESS_TOKEN=token)
            return _stdio_session(self.command, self.stdio_args, env)
        headers = {"Authorization": f"Bearer {token}"}
        if self.toolsets:  # hosted server: select the toolsets we want (projects, issues)
            headers["X-MCP-Toolsets"] = self.toolsets
        return _http_session(self.url, headers)

    async def _call(self, tool: str, args: dict, retry_transient: bool = True) -> Any:
        """Invoke an MCP tool, retrying transient transport failures (timeouts,
        connection resets) with a short backoff. ``retry_transient=False`` for
        writes — a write that succeeded but whose response timed out must not be
        re-sent (that would double-post a comment / double-apply a move)."""
        tries = 3 if retry_transient else 1
        last: Exception | None = None
        for attempt in range(tries):
            try:
                async with self.session_factory() as session:
                    return await session.call_tool(tool, args)
            except BoardError:
                raise
            except Exception as e:  # transport / protocol failures
                last = e
                if attempt < tries - 1:
                    await asyncio.sleep(0.4 * (attempt + 1))
        raise BoardError(f"GitHub MCP '{tool}' failed: {last}", status=502)

    # ── BoardProvider: read ────────────────────────────────────────

    def list_boards(self) -> list[dict]:
        # The connect flow builds the board from config, so this returns the
        # single board this provider instance is configured for.
        if self.kind == "project":
            num = self.project_number
            return [{
                "id": f"github-project:{self.owner}/{num}",
                "name": f"{self.owner} project #{num}",
                "kind": "project",
                "source_url": f"https://github.com/users/{self.owner}/projects/{num}",
            }]
        return [
            {
                "id": f"github:{self._scope}",
                "name": self._scope,
                "kind": "issues",
                "source_url": f"https://github.com/{self._scope}",
            }
        ]

    async def list_items(self, board: dict | None = None) -> list[BoardItem]:
        b = board or self.board
        if self.kind == "project":
            return await self._list_project_items(b)
        return await self._list_issue_items(b)

    async def _list_project_items(self, board: dict) -> list[BoardItem]:
        if not self.owner or not self.project_number:
            raise BoardError("github-mcp project board needs config {owner, project_number}", status=400)
        args: dict[str, Any] = {
            "method": "list_project_items",
            "owner": self.owner,
            "project_number": int(self.project_number),
            "field_names": ["Status"],
        }
        result = await self._call("projects_list", args)
        data = _result_json(result)
        items: list[BoardItem] = []
        for it in _extract_items(data):
            items.append(_item_from_project(self._scope, it))
        # list_project_items paginates via after/before cursors — follow them.
        pi = _extract_page_info(data)
        cursor = pi.get("endCursor") or pi.get("next")
        while cursor and len(items) < 1000:
            args["after"] = cursor
            result = await self._call("projects_list", args)
            data = _result_json(result)
            page = _extract_items(data)
            if not page:
                break
            items.extend(_item_from_project(self._scope, it) for it in page)
            pi = _extract_page_info(data)
            cursor = pi.get("endCursor") or pi.get("next")
        return items

    async def _list_issue_items(self, board: dict) -> list[BoardItem]:
        if not self.owner or not self.repo:
            raise BoardError("github-mcp board config needs {owner, repo}", status=400)
        state = (board.get("config") or {}).get("state")  # OPEN|CLOSED|None
        args: dict[str, Any] = {"owner": self.owner, "repo": self.repo, "perPage": 100}
        if state:
            args["state"] = str(state).upper()

        items: list[BoardItem] = []
        for _ in range(_MAX_PAGES):
            result = await self._call("list_issues", args)
            data = _result_json(result)
            page = _extract_items(data)
            for it in page:
                if it.get("pull_request"):  # issue API can include PRs — skip them
                    continue
                items.append(_item_from_issue(self._scope, it))
            pi = _extract_page_info(data)
            if not pi.get("hasNextPage") or not pi.get("endCursor"):
                break
            args["after"] = pi["endCursor"]
        return items

    async def get_item(self, board: dict | None, item_id: str) -> BoardItem | None:
        if self.kind == "project":
            result = await self._call(
                "projects_get",
                {
                    "method": "get_project_item",
                    "owner": self.owner,
                    "project_number": int(self.project_number),
                    "item_id": _project_item_id_from(item_id),
                    "field_names": ["Status"],
                },
            )
            data = _result_json(result)
            if isinstance(data, dict) and data.get("id"):
                return _item_from_project(self._scope, data)
            return None
        result = await self._call(
            "issue_read",
            {
                "method": "get",
                "owner": self.owner,
                "repo": self.repo,
                "issue_number": _number_from_id(item_id),
            },
        )
        data = _result_json(result)
        if isinstance(data, dict) and data:
            return _item_from_issue(self._scope, data)
        return None

    async def capabilities(self, board: dict | None = None) -> dict:
        """What the current token can do on this board: ``{move, comment, scopes}``.

        ``move`` (change a card's Status) needs the ``project`` scope; ``comment``
        (write to the underlying issue) needs ``repo``. Used to gate the UI so a
        project-only token doesn't offer a comment box that silently fails. An
        invalid token surfaces ``error`` with the GitHub API's details.
        """
        scopes, error = await asyncio.to_thread(_github_token_check, _resolve_token())
        return _capabilities_for(scopes, error)

    def token_status(self) -> dict:
        """Pre-flight check before connecting: ``{present, valid, scopes, error}``.

        ``valid`` is False when the token is missing, rejected, or unreachable —
        the error message carries the GitHub API's details. Call this before
        making any MCP call so a bad token fails fast with a clear message.
        """
        token = _resolve_token()
        if not token:
            return {
                "present": False, "valid": False, "scopes": [],
                "error": "No GitHub token configured. Set GITHUB_PERSONAL_ACCESS_TOKEN "
                         "(or run `gh auth token` locally).",
            }
        scopes, error = _github_token_check(token)
        return {"present": True, "valid": not error, "scopes": sorted(scopes), "error": error}

    async def project_title(self, board: dict | None = None) -> str:
        """The real display title of a GitHub Project (e.g. "Constellation Board")."""
        if self.kind != "project":
            return ""
        result = await self._call(
            "projects_list", {"method": "list_projects", "owner": self.owner}
        )
        data = _result_json(result)
        for p in ((data.get("projects") or []) if isinstance(data, dict) else []):
            if str(p.get("number")) == str(self.project_number):
                return p.get("title") or ""
        return ""

    async def status_options(self, board: dict | None = None) -> list[dict]:
        """The board's Status field options — the full set of swim lanes.

        Only meaningful for project boards (issues boards use Open/Closed).
        Returns ``[{"name": "Backlog", "color": "GREEN"}, ...]``.
        """
        if self.kind != "project":
            return []
        result = await self._call(
            "projects_list",
            {
                "method": "list_project_fields",
                "owner": self.owner,
                "project_number": int(self.project_number),
            },
        )
        data = _result_json(result)
        fields = (
            (data.get("project_fields") or data.get("fields") or [])
            if isinstance(data, dict)
            else []
        )
        for f in fields:
            if str(f.get("name", "")).lower() == "status":
                opts = []
                for o in f.get("options") or []:
                    name = o.get("name")
                    if isinstance(name, dict):
                        name = name.get("raw") or name.get("html") or ""
                    opts.append({"name": str(name), "color": o.get("color") or ""})
                return opts
        return []

    # ── BoardProvider: write (Phase 2) ─────────────────────────────

    async def update_item(self, board: dict | None, item_id: str, patch: dict) -> BoardItem:
        if self.kind == "project":
            # Move a card: projects_write update_project_item on the Status field.
            if "status" not in patch:
                raise BoardError("project boards only support the status field right now", status=400)
            result = await self._call(
                "projects_write",
                {
                    "method": "update_project_item",
                    "owner": self.owner,
                    "project_number": int(self.project_number),
                    "item_id": _project_item_id_from(item_id),
                    "updated_field": {"name": "Status", "value": str(patch["status"])},
                },
                retry_transient=False,
            )
            _result_json(result)  # surface write errors instead of pretending success
            return await self.get_item(board, item_id) or BoardItem(id=item_id)

        args: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.repo,
            "issue_number": _number_from_id(item_id),
            "method": "update",
        }
        if "status" in patch:  # normalize to lowercase GitHub state
            args["state"] = "closed" if str(patch["status"]).lower() == "closed" else "open"
        for key in ("title", "body", "labels", "assignees"):
            if key in patch:
                args[key] = patch[key]
        result = await self._call("issue_write", args, retry_transient=False)
        data = _result_json(result)
        if isinstance(data, dict) and data.get("number") is not None:
            return _item_from_issue(self._scope, data)
        # Some deployments return only an ack — refetch the canonical state.
        return await self.get_item(board, item_id) or BoardItem(id=item_id)

    async def create_item(
        self, board: dict | None, title: str, body: str = "",
        labels: list | None = None, status: str = "",
    ) -> BoardItem:
        """Create a new issue and add it to the board (project), or create an
        issue directly (issues board). Returns the new board item."""
        labels = labels or []
        # The issue is created in the repo the board's items live in. Project
        # boards aggregate items from a repo — derive it from an existing item;
        # fall back to the board config's repo when the board is empty.
        repo = self.repo
        if self.kind == "project":
            if not repo:
                items = (board or self.board).get("items") or []
                for it in items:
                    r = (it.get("raw", {}).get("content") or {}).get("repository") or ""
                    if r:
                        repo = r
                        break
            if not repo:
                raise BoardError(
                    "Can't tell which repo to create the issue in — sync the board "
                    "first (it learns the repo from existing items) or set 'repo' "
                    "in the board config.",
                    status=400,
                )
        if not self.owner or not repo:
            raise BoardError("create needs an owner and repo", status=400)
        if "/" in repo:
            iowner, _, irepo = repo.partition("/")
        else:
            iowner, irepo = self.owner, repo

        # Duplicate guard: if an OPEN issue with this exact title already exists,
        # return it instead of creating another. The AI chat retries failed tool
        # calls, so without this a timeout-after-success would mint duplicates.
        # Only OPEN issues count as duplicates — a closed issue with the same
        # title must not block a new issue (nor surface a stray "closed" lane).
        try:
            existing = await self._call(
                "search_issues",
                {"query": title, "owner": iowner, "repo": irepo, "perPage": 30},
            )
            hits = _extract_items(_result_json(existing))
        except BoardError:
            hits = []  # search unavailable — fall through to create
        for hit in hits:
            if str(hit.get("state") or "open").lower() != "open":
                continue  # only an open issue is a real duplicate
            if html.unescape(hit.get("title") or "").strip() == title.strip():
                dup = _item_from_issue(f"{iowner}/{irepo}", hit)
                # already on a project board? return the card form.
                if self.kind == "project":
                    refreshed = await self._list_project_items(board or self.board)
                    for it in refreshed:
                        if (it.raw.get("content") or {}).get("number") == dup.number:
                            return it
                return dup

        # 1) create the issue
        create_args: dict[str, Any] = {
            "owner": iowner,
            "repo": irepo,
            "method": "create",
            "title": title,
        }
        if body:
            create_args["body"] = body
        if labels:
            create_args["labels"] = labels
        result = await self._call("issue_write", create_args, retry_transient=False)
        data = _result_json(result)
        # The create response's envelope varies (flat issue / wrapped / ack) —
        # resolve the new issue's number defensively.
        issue_number = None
        created = data if isinstance(data, dict) else {}
        for candidate in (
            created,                                  # flat: {"number": 99, ...}
            created.get("issue") or {},               # wrapped: {"issue": {...}}
            created.get("data") or {},                # {"data": {...}}
            created.get("item") or {},
        ):
            if isinstance(candidate, dict) and candidate.get("number") is not None:
                issue_number = candidate["number"]
                created = candidate
                break
        if issue_number is None:
            # Observed shape: {"id": "5151007331", "url": ".../issues/81"} —
            # no number field, but the issue number is in the URL.
            import re as _re
            url = created.get("url") or created.get("html_url") or ""
            m = _re.search(r"/issues/(\d+)", url)
            if m:
                issue_number = int(m.group(1))
                created["number"] = issue_number  # for _item_from_issue below
        if issue_number is None:
            raise BoardError(
                "Issue was created but its number couldn't be read from the "
                f"response: {str(data)[:200]}",
                status=502,
            )

        # 2) for a project board, add the issue to the project
        if self.kind == "project":
            add_result = await self._call(
                "projects_write",
                {
                    "method": "add_project_item",
                    "owner": self.owner,
                    "project_number": int(self.project_number),
                    "item_type": "issue",
                    "item_owner": iowner,
                    "item_repo": irepo,
                    "issue_number": issue_number,
                },
                retry_transient=False,
            )
            add_data = _result_json(add_result)
            add = add_data if isinstance(add_data, dict) else {}
            node_id = add.get("node_id") or add.get("id") or ""
            # The lane update accepts either the numeric item id or (better)
            # the issue reference — the GraphQL node-id string can't be int()'d,
            # so resolve by owner/repo/issue_number instead (format-independent).
            if status:
                await self._call(
                    "projects_write",
                    {
                        "method": "update_project_item",
                        "owner": self.owner,
                        "project_number": int(self.project_number),
                        "item_owner": iowner,
                        "item_repo": irepo,
                        "issue_number": issue_number,
                        "updated_field": {"name": "Status", "value": str(status)},
                    },
                    retry_transient=False,
                )
            # Refresh from the board so the returned card reflects the project.
            refreshed = await self._list_project_items(board or self.board)
            for it in refreshed:
                content = (it.raw.get("content") or {})
                if content.get("number") == issue_number:
                    return it
            _ = node_id  # (kept for debugging; resolution no longer needs it)
        # Fall-through (issues board, or the refresh missed the new card). A
        # project board's card must never carry the issue's open/closed state as
        # its swim-lane status — that would mint a stray "open"/"closed" column.
        item = _item_from_issue(f"{iowner}/{irepo}", created)
        if self.kind == "project":
            item.status = status or ""  # requested lane, or none (not open/closed)
        return item

    async def add_comment(self, board: dict | None, item_id: str, body: str) -> dict:
        if self.kind == "project":
            # Comments live on the underlying issue; resolve repo + number from the item.
            item = await self.get_item(board, item_id)
            if item is None:
                raise BoardError(f"item {item_id} not found", status=404)
            repo = (item.raw.get("content") or {}).get("repository") or ""
            issue_number = (item.raw.get("content") or {}).get("number")
            if not repo or not issue_number:
                raise BoardError("project item has no underlying issue to comment on", status=400)
            iowner, _, irepo = repo.partition("/")
            result = await self._call(
                "add_issue_comment",
                {"owner": iowner, "repo": irepo, "issue_number": int(issue_number), "body": body},
                retry_transient=False,
            )
            _result_json(result)  # surface write errors (e.g. token lacks repo scope)
            return {"ok": True, "item_id": item_id}
        result = await self._call(
            "add_issue_comment",
            {
                "owner": self.owner,
                "repo": self.repo,
                "issue_number": _number_from_id(item_id),
                "body": body,
            },
            retry_transient=False,
        )
        _result_json(result)
        return {"ok": True, "item_id": item_id}


# ── defensive payload shims (the MCP server's exact envelope can vary) ───


def _extract_items(data: Any) -> list[dict]:
    """Pull the list of issue dicts out of a list_issues response."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("items", "issues", "nodes", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return [d for d in val if isinstance(d, dict)]
        # single issue object wrapped (unlikely for list) — ignore
    return []


def _extract_page_info(data: Any) -> dict:
    """Extract {hasNextPage, endCursor} from wherever the server puts it."""
    if isinstance(data, dict):
        for key in ("pageInfo", "pagination", "page_info"):
            pi = data.get(key)
            if isinstance(pi, dict):
                return {
                    "hasNextPage": pi.get("hasNextPage") or pi.get("has_next_page"),
                    "endCursor": pi.get("endCursor") or pi.get("end_cursor") or pi.get("next"),
                }
    return {}
