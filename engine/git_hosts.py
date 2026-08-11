"""Universal git-host import — resolve an org/workspace/team-project link on a
supported git host into its list of repositories.

Supported hosts (public repos, unauthenticated):
  github.com      → API /orgs|users/{owner}/repos (with optional gh token)
  gitlab.com      → API /groups|users/{path}/projects
  bitbucket.org   → API /repositories/{workspace}
  dev.azure.com   → API /_apis/projects + git/repositories

The link parsers are pure functions; each provider's fetcher takes an
injectable opener (``urllib.request.urlopen`` by default) so the whole module
is unit-testable offline with fixture payloads. Only the provider's fixed API
base is ever contacted — the link is used solely to extract a validated owner
identifier, keeping the arbitrary-network surface closed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional


# ── Errors ─────────────────────────────────────────────────────────


class GitHostError(Exception):
    """Raised for parse/fetch failures.

    ``status`` mirrors the HTTP status when one was observed (None for
    invalid links / network failures).
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


# ── Link parsing (pure) ────────────────────────────────────────────

# Owner identifiers are strictly constrained per host so nothing but a safe
# path segment can reach an API URL.
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_GITLAB_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_AZURE_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .()\-_]*$")

_MAX_REPOS = 500


def _host_of(url: str) -> Optional[str]:
    """Lowercased hostname of an http(s) URL, or None when unparseable."""
    try:
        parts = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower()


def parse_link(url: str) -> Optional[dict]:
    """Parse a git-host project link into ``{provider, owner, project?}``.

    Returns None when the URL is not a supported org-level link (wrong
    host, extra path segments, invalid characters, missing scheme…).
    """
    host = _host_of(url)
    if not host:
        return None
    parts = urllib.parse.urlparse(url.strip())
    raw_path = urllib.parse.unquote(parts.path)
    # Reject empty (``//``) and dot (``.``/``..``) segments outright — they
    # could alias other paths after collapsing.
    if "//" in raw_path or "/./" in raw_path or "/../" in raw_path:
        return None
    segs = [s for s in raw_path.strip("/").split("/") if s]

    if host in ("github.com", "www.github.com"):
        if len(segs) != 1 or not _OWNER_RE.match(segs[0]):
            return None
        return {"provider": "github", "owner": segs[0]}

    if host in ("gitlab.com", "www.gitlab.com"):
        path = "/".join(segs)
        if not segs or not _GITLAB_PATH_RE.match(path):
            return None
        return {"provider": "gitlab", "owner": path}

    if host in ("bitbucket.org", "www.bitbucket.org"):
        if len(segs) != 1 or not _OWNER_RE.match(segs[0]):
            return None
        return {"provider": "bitbucket", "owner": segs[0]}

    if host == "dev.azure.com" or host.endswith(".visualstudio.com"):
        org = host[: -len(".visualstudio.com")] if host.endswith(".visualstudio.com") else (segs.pop(0) if segs else "")
        if not _OWNER_RE.match(org):
            return None
        project = segs[0] if segs else ""
        if len(segs) > 1 or (project and not _AZURE_PROJECT_RE.match(project)):
            return None
        return {"provider": "azure-devops", "owner": org, "project": project or None}

    return None


# ── HTTP helper (injectable opener) ────────────────────────────────


class _DefaultOpener:
    """Default opener — plain ``urllib.request.urlopen`` behind ``.open``.

    Fetchers always call ``opener.open(req, timeout=...)``; tests inject a
    fake opener with the same surface.
    """

    def open(self, req, timeout: int = 20):
        return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url: str, opener, headers: dict, label: str):
    """GET a URL via the opener and parse the JSON body.

    Returns ``(response, parsed_json)``. Raises GitHostError with a
    friendly, status-carrying message on failure.
    """
    req = urllib.request.Request(url, headers=headers)
    req.add_header("User-Agent", "Constellation/0.1")
    try:
        resp = opener.open(req, timeout=20)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise GitHostError(f"{label} not found", status=404)
        if e.code in (401, 403):
            raise GitHostError(
                f"{label}: access denied (private repos need credentials, or the API rate limit was hit)",
                status=e.code,
            )
        if e.code == 429:
            raise GitHostError(f"{label}: API rate limit reached — try again later", status=429)
        raise GitHostError(f"{label}: HTTP {e.code}", status=e.code)
    except urllib.error.URLError as e:
        raise GitHostError(f"Could not reach {label}: {e.reason}", status=502)
    except TimeoutError:
        raise GitHostError(f"Timed out reaching {label}", status=502)
    try:
        return resp, json.loads(resp.read().decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        raise GitHostError(
            f"{label}: unexpected response from the API (the workspace may be private — "
            "private repos need credentials, which this build only has for GitHub)"
        )


# ── Provider adapters ──────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"


def fetch_github(owner: str, opener, token: Optional[str] = None) -> list[dict]:
    """List an org's (or user's) repos via the GitHub REST API.

    Tries ``/orgs/{owner}/repos`` first (orgs are not reachable through
    ``/users``), falling back to ``/users/{owner}/repos`` on 404.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for scope in ("orgs", "users"):
        try:
            resp, items = _get_json(
                f"{_GITHUB_API}/{scope}/{urllib.parse.quote(owner)}/repos?per_page=100",
                opener, headers, f"GitHub {scope}/{owner}",
            )
            break
        except GitHostError as e:
            if scope == "orgs" and e.status == 404:
                continue
            raise
    else:
        raise GitHostError(f"GitHub owner '{owner}' not found", status=404)

    repos = [_github_repo(it) for it in items]
    link = (resp.headers or {}).get("Link") or ""
    while link and len(repos) < _MAX_REPOS:
        m = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
        if not m:
            break
        resp, items = _get_json(m.group(1), opener, headers, f"GitHub {scope}/{owner}")
        repos.extend(_github_repo(it) for it in items)
        link = (resp.headers or {}).get("Link") or ""
    return repos


def _github_repo(it: dict) -> dict:
    return {
        "name": it.get("name") or "",
        "full_name": it.get("full_name") or "",
        "description": it.get("description") or "",
        "default_branch": it.get("default_branch") or "",
        "clone_url": it.get("clone_url") or "",
    }


_GITLAB_API = "https://gitlab.com/api/v4"


def fetch_gitlab(owner: str, opener) -> list[dict]:
    """List a GitLab group's (or user's) projects.

    Tries the group endpoint first (nested groups use a slash-joined
    path), falling back to a username lookup + ``/users/{id}/projects``
    on 404.
    """
    quoted = urllib.parse.quote(owner, safe="/")
    try:
        resp, items = _get_json(
            f"{_GITLAB_API}/groups/{quoted}/projects?per_page=100",
            opener, {}, f"GitLab group '{owner}'",
        )
        base = f"{_GITLAB_API}/groups/{quoted}/projects"
    except GitHostError as e:
        if e.status != 404:
            raise
        _, users = _get_json(
            f"{_GITLAB_API}/users?username={urllib.parse.quote(owner)}",
            opener, {}, f"GitLab user '{owner}'",
        )
        if not users:
            raise GitHostError(f"GitLab group or user '{owner}' not found", status=404)
        uid = users[0].get("id")
        resp, items = _get_json(
            f"{_GITLAB_API}/users/{uid}/projects?per_page=100",
            opener, {}, f"GitLab user '{owner}'",
        )
        base = f"{_GITLAB_API}/users/{uid}/projects"

    repos = [_gitlab_repo(it) for it in items]
    total_pages = (resp.headers or {}).get("X-Total-Pages") or ""
    page = 2
    while total_pages.isdigit() and page <= int(total_pages) and len(repos) < _MAX_REPOS:
        _, items = _get_json(
            f"{base}?per_page=100&page={page}", opener, {}, f"GitLab {owner}",
        )
        repos.extend(_gitlab_repo(it) for it in items)
        page += 1
    return repos


def _gitlab_repo(it: dict) -> dict:
    return {
        "name": it.get("name") or "",
        "full_name": it.get("path_with_namespace") or "",
        "description": it.get("description") or "",
        "default_branch": it.get("default_branch") or "",
        "clone_url": it.get("http_url_to_repo") or "",
    }


_BITBUCKET_API = "https://api.bitbucket.org/2.0"


def fetch_bitbucket(owner: str, opener) -> list[dict]:
    """List a Bitbucket Cloud workspace's repositories (``next`` pagination)."""
    url = f"{_BITBUCKET_API}/repositories/{urllib.parse.quote(owner)}?pagelen=100"
    repos: list[dict] = []
    while url and len(repos) < _MAX_REPOS:
        resp, data = _get_json(url, opener, {}, f"Bitbucket workspace '{owner}'")
        for it in data.get("values") or []:
            repos.append(_bitbucket_repo(it))
        url = data.get("next") or ""
    return repos


def _bitbucket_repo(it: dict) -> dict:
    clone = ""
    for c in (it.get("links") or {}).get("clone") or []:
        if c.get("name") == "https":
            clone = c.get("href") or ""
            break
    return {
        "name": it.get("name") or "",
        "full_name": it.get("full_name") or "",
        "description": it.get("description") or "",
        "default_branch": (it.get("mainbranch") or {}).get("name") or "",
        "clone_url": clone,
    }


_AZURE_API = "https://dev.azure.com"


def fetch_azure(owner: str, project: Optional[str], opener) -> list[dict]:
    """List Azure DevOps repositories.

    With a team project → that project's repos directly. Org-level →
    enumerate the org's team projects, then flatten each project's repos
    (``full_name`` = ``{project}/{repo}``).
    """
    headers = {"Accept": "application/json"}
    if project:
        url = (
            f"{_AZURE_API}/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(project, safe='')}/_apis/git/repositories?api-version=7.1"
        )
        _, data = _get_json(url, opener, headers, f"Azure DevOps project '{owner}/{project}'")
        return [_azure_repo(it, project) for it in data.get("value") or []]

    _, data = _get_json(
        f"{_AZURE_API}/{urllib.parse.quote(owner)}/_apis/projects?api-version=7.1&$top=50",
        opener, headers, f"Azure DevOps org '{owner}'",
    )
    projects = data.get("value") or []
    if not projects:
        raise GitHostError(f"Azure DevOps org '{owner}' not found or has no accessible projects", status=404)
    repos: list[dict] = []
    for tp in projects:
        tname = tp.get("name") or ""
        url = (
            f"{_AZURE_API}/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(tname, safe='')}/_apis/git/repositories?api-version=7.1"
        )
        _, data = _get_json(url, opener, headers, f"Azure DevOps project '{owner}/{tname}'")
        for it in data.get("value") or []:
            repos.append(_azure_repo(it, tname))
            if len(repos) >= _MAX_REPOS:
                return repos
    return repos


def _azure_repo(it: dict, project: str) -> dict:
    default = it.get("defaultBranch") or ""
    if default.startswith("refs/heads/"):
        default = default[len("refs/heads/"):]
    return {
        "name": it.get("name") or "",
        "full_name": f"{project}/{it.get('name') or ''}" if project else (it.get("name") or ""),
        "description": "",
        "default_branch": default,
        "clone_url": it.get("remoteUrl") or "",
    }


# ── Dispatch ───────────────────────────────────────────────────────

_PROVIDER_FETCHERS: dict[str, Callable] = {
    "github": fetch_github,
    "gitlab": fetch_gitlab,
    "bitbucket": fetch_bitbucket,
    "azure-devops": fetch_azure,
}


def fetch_repos(link: str, opener=None, token: Optional[str] = None) -> dict:
    """Resolve a git-host link into ``{provider, owner, repos}``.

    Raises GitHostError for unsupported links and host API failures.
    """
    parsed = parse_link(link)
    if not parsed:
        raise GitHostError(
            "Unsupported link — paste an org/workspace link from github.com, "
            "gitlab.com, bitbucket.org or dev.azure.com (or add the repos manually)"
        )
    opener = opener or _DefaultOpener()
    provider = parsed["provider"]
    if provider == "github":
        repos = fetch_github(parsed["owner"], opener, token=token)
    elif provider == "azure-devops":
        repos = fetch_azure(parsed["owner"], parsed.get("project"), opener)
    else:
        repos = _PROVIDER_FETCHERS[provider](parsed["owner"], opener)
    return {"provider": provider, "owner": parsed["owner"], "repos": repos}


def github_token() -> str:
    """Best-effort GitHub credential: ``GITHUB_TOKEN``/``GH_TOKEN`` env, else
    ``gh auth token`` when the GitHub CLI is installed and authenticated."""
    for env in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = os.environ.get(env, "").strip()
        if tok:
            return tok
    exe = shutil.which("gh") or shutil.which("gh.exe")
    if not exe and os.name == "nt":
        probe = Path(os.environ.get("ProgramFiles", "")) / "GitHub CLI" / "gh.exe"
        if probe.exists():
            exe = str(probe)
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "auth", "token"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""
