"""Universal git-host import — link parsers, provider adapters, dispatch.

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps). No network: all
fetch tests drive an injected fake opener with fixture payloads.
"""
from __future__ import annotations
import io
import json
import urllib.error

from engine.git_hosts import GitHostError, fetch_repos, parse_link


# ── Fake HTTP layer ────────────────────────────────────────────────


class FakeResponse:
    """Stand-in for urllib's response: status, headers dict, read()."""

    def __init__(self, payload, status=200, headers=None):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._payload


def http404(url):
    """A real urllib.error.HTTPError so the adapter's except clauses match."""
    return urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))


class FakeOpener:
    """Scripted opener: a plan of (url-substring predicate, response-or-raise)."""

    def __init__(self, plan=None):
        self.plan = list(plan or [])
        self.calls = []  # [(url, headers)]

    def open(self, req, timeout=20):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append((url, dict(req.header_items())))
        for pred, result in self.plan:
            if pred(url):
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError("No plan entry for " + url)

    def urls(self):
        return [u for u, _ in self.calls]

    def headers_of(self, url_substr):
        for u, h in self.calls:
            if url_substr in u:
                return h
        return None


def has(substr):
    return lambda url: substr in url


def ghp(payload, **kw):
    return FakeResponse(payload, **kw)


# ── Link parsing ───────────────────────────────────────────────────


def test_parse_github_links():
    assert parse_link("https://github.com/acme") == {"provider": "github", "owner": "acme"}
    assert parse_link("https://www.github.com/acme/") == {"provider": "github", "owner": "acme"}
    assert parse_link("http://github.com/acme")["owner"] == "acme"
    assert parse_link("https://github.com/acme?tab=repositories") == {"provider": "github", "owner": "acme"}


def test_parse_github_rejects():
    assert parse_link("https://github.com/acme/order-service") is None  # repo link, not org
    assert parse_link("https://github.com/acme/orgs/team") is None
    assert parse_link("https://github.com/") is None
    assert parse_link("https://github.com/a b") is None
    assert parse_link("github.com/acme") is None  # no scheme
    assert parse_link("https://evil.com/acme") is None
    assert parse_link("https://github.com/acme%2F..") is None


def test_parse_gitlab_links():
    assert parse_link("https://gitlab.com/acme") == {"provider": "gitlab", "owner": "acme"}
    assert parse_link("https://gitlab.com/acme/team/sub") == {"provider": "gitlab", "owner": "acme/team/sub"}
    assert parse_link("https://gitlab.com/my_group.1/sub")["owner"] == "my_group.1/sub"
    # Nested groups make repo links ambiguous — any dot-separated path is a
    # valid group path, so a repo-style link resolves as a (maybe empty) group.
    assert parse_link("https://gitlab.com/acme/order-service")["owner"] == "acme/order-service"
    assert parse_link("https://gitlab.com/acme//x") is None
    assert parse_link("https://gitlab.com/-") is None
    assert parse_link("https://gitlab.com/acme/../x") is None


def test_parse_bitbucket_links():
    assert parse_link("https://bitbucket.org/acme") == {"provider": "bitbucket", "owner": "acme"}
    assert parse_link("https://www.bitbucket.org/acme/") == {"provider": "bitbucket", "owner": "acme"}
    assert parse_link("https://bitbucket.org/acme/repo") is None
    assert parse_link("https://bitbucket.org/acme/repo/src") is None


def test_parse_azure_links():
    assert parse_link("https://dev.azure.com/acme") == {
        "provider": "azure-devops", "owner": "acme", "project": None,
    }
    assert parse_link("https://dev.azure.com/acme/My%20Project") == {
        "provider": "azure-devops", "owner": "acme", "project": "My Project",
    }
    assert parse_link("https://acme.visualstudio.com/Team%20A") == {
        "provider": "azure-devops", "owner": "acme", "project": "Team A",
    }
    assert parse_link("https://acme.visualstudio.com") == {
        "provider": "azure-devops", "owner": "acme", "project": None,
    }
    assert parse_link("https://dev.azure.com/acme/p1/p2") is None
    assert parse_link("https://dev.azure.com/acme/bad!org") is None
    assert parse_link("https://dev.azure.com/") is None


# ── GitHub adapter ─────────────────────────────────────────────────


def _gh_org_payload():
    return [
        {
            "name": "order-service",
            "full_name": "acme/order-service",
            "description": "Orders",
            "default_branch": "main",
            "clone_url": "https://github.com/acme/order-service.git",
        },
        {
            "name": "payment-service",
            "full_name": "acme/payment-service",
            "description": None,
            "default_branch": "develop",
            "clone_url": "https://github.com/acme/payment-service.git",
        },
    ]


def test_github_fetch_org():
    opener = FakeOpener([(has("orgs/acme/repos"), ghp(_gh_org_payload()))])
    out = fetch_repos("https://github.com/acme", opener=opener)
    assert out["provider"] == "github" and out["owner"] == "acme"
    names = [r["name"] for r in out["repos"]]
    assert names == ["order-service", "payment-service"]
    assert out["repos"][0]["clone_url"] == "https://github.com/acme/order-service.git"
    assert out["repos"][1]["default_branch"] == "develop"
    assert out["repos"][1]["description"] == ""
    assert not any("users/acme" in u for u in opener.urls())


def test_github_fetch_falls_back_to_user():
    opener = FakeOpener([
        (has("orgs/acme/repos"), http404("https://api.github.com/orgs/acme/repos")),
        (has("users/acme/repos"), ghp(_gh_org_payload())),
    ])
    out = fetch_repos("https://github.com/acme", opener=opener)
    assert [r["name"] for r in out["repos"]] == ["order-service", "payment-service"]
    assert any("orgs/acme" in u for u in opener.urls())
    assert any("users/acme" in u for u in opener.urls())


def test_github_fetch_not_found_raises():
    opener = FakeOpener([
        (has("orgs/acme/repos"), http404("orgs")),
        (has("users/acme/repos"), http404("users")),
    ])
    try:
        fetch_repos("https://github.com/acme", opener=opener)
        raise AssertionError("expected GitHostError")
    except GitHostError as e:
        assert e.status == 404 and "not found" in str(e)


def test_github_fetch_pagination():
    page1 = ghp(
        _gh_org_payload(),
        headers={"Link": '<https://api.github.com/orgs/acme/repos?per_page=100&page=2>; rel="next"'},
    )
    page2 = ghp([{"name": "auth-service", "full_name": "acme/auth-service", "clone_url": "https://github.com/acme/auth-service.git"}])
    opener = FakeOpener([(has("page=2"), page2), (has("repos?per_page=100"), page1)])
    out = fetch_repos("https://github.com/acme", opener=opener)
    assert [r["name"] for r in out["repos"]] == ["order-service", "payment-service", "auth-service"]
    assert any("page=2" in u for u in opener.urls())


def test_github_fetch_sends_token():
    opener = FakeOpener([(has("orgs/acme/repos"), ghp(_gh_org_payload()))])
    fetch_repos("https://github.com/acme", opener=opener, token="sekrit")
    auth = opener.headers_of("orgs/acme/repos").get("Authorization")
    assert auth == "Bearer sekrit"


# ── GitLab adapter ─────────────────────────────────────────────────


def _gl_payload():
    return [
        {
            "name": "order-service",
            "path_with_namespace": "acme/team/order-service",
            "description": "Orders",
            "default_branch": "main",
            "http_url_to_repo": "https://gitlab.com/acme/team/order-service.git",
        }
    ]


def test_gitlab_fetch_group():
    opener = FakeOpener([(has("groups/acme/team/projects"), ghp(_gl_payload(), headers={"X-Total-Pages": "1"}))])
    out = fetch_repos("https://gitlab.com/acme/team", opener=opener)
    assert out["provider"] == "gitlab" and out["owner"] == "acme/team"
    assert out["repos"][0]["full_name"] == "acme/team/order-service"
    assert out["repos"][0]["clone_url"] == "https://gitlab.com/acme/team/order-service.git"


def test_gitlab_fetch_user_fallback():
    opener = FakeOpener([
        (has("groups/jdoe/projects"), http404("groups")),
        (has("users?username=jdoe"), ghp([{"id": 7, "username": "jdoe"}])),
        (has("users/7/projects"), ghp(_gl_payload(), headers={"X-Total-Pages": "1"})),
    ])
    out = fetch_repos("https://gitlab.com/jdoe", opener=opener)
    assert out["repos"][0]["name"] == "order-service"
    assert any("users/7/projects" in u for u in opener.urls())


def test_gitlab_fetch_unknown_raises():
    opener = FakeOpener([
        (has("groups/ghost/projects"), http404("groups")),
        (has("users?username=ghost"), ghp([])),
    ])
    try:
        fetch_repos("https://gitlab.com/ghost", opener=opener)
        raise AssertionError("expected GitHostError")
    except GitHostError as e:
        assert e.status == 404


def test_gitlab_fetch_pagination():
    opener = FakeOpener([
        (has("page=2"), ghp(_gl_payload())),
        (has("groups/acme/projects"), ghp(_gl_payload(), headers={"X-Total-Pages": "2"})),
    ])
    out = fetch_repos("https://gitlab.com/acme", opener=opener)
    assert len(out["repos"]) == 2
    assert any("page=2" in u for u in opener.urls())


# ── Bitbucket adapter ──────────────────────────────────────────────


def _bb_payload(**over):
    repo = {
        "name": "order-service",
        "full_name": "acme/order-service",
        "description": "Orders",
        "mainbranch": {"name": "main"},
        "links": {"clone": [{"name": "https", "href": "https://bitbucket.org/acme/order-service.git"}]},
    }
    repo.update(over)
    return {"values": [repo], "next": None}


def test_bitbucket_fetch_workspace():
    opener = FakeOpener([(has("repositories/acme"), ghp(_bb_payload()))])
    out = fetch_repos("https://bitbucket.org/acme", opener=opener)
    assert out["repos"][0]["clone_url"] == "https://bitbucket.org/acme/order-service.git"
    assert out["repos"][0]["default_branch"] == "main"


def test_bitbucket_fetch_missing_fields():
    repo = {
        "name": "legacy",
        "full_name": "acme/legacy",
        "description": None,
        "mainbranch": None,
        "links": {"clone": [{"name": "ssh", "href": "ssh://git@bitbucket.org/acme/legacy.git"}]},
    }
    opener = FakeOpener([(has("repositories/acme"), ghp({"values": [repo], "next": None}))])
    out = fetch_repos("https://bitbucket.org/acme", opener=opener)
    assert out["repos"][0]["default_branch"] == ""
    assert out["repos"][0]["clone_url"] == ""  # no https clone link


def test_bitbucket_fetch_pagination():
    p1 = _bb_payload()
    p1["next"] = "https://api.bitbucket.org/2.0/repositories/acme?pagelen=100&page=2"
    opener = FakeOpener([
        (has("page=2"), ghp(_bb_payload(name="payment-service", full_name="acme/payment-service"))),
        (has("repositories/acme"), ghp(p1)),
    ])
    out = fetch_repos("https://bitbucket.org/acme", opener=opener)
    assert [r["name"] for r in out["repos"]] == ["order-service", "payment-service"]


def test_bitbucket_fetch_404_raises():
    opener = FakeOpener([(has("repositories/ghost"), http404("ghost"))])
    try:
        fetch_repos("https://bitbucket.org/ghost", opener=opener)
        raise AssertionError("expected GitHostError")
    except GitHostError as e:
        assert e.status == 404


# ── Azure DevOps adapter ───────────────────────────────────────────


def _azure_repo_payload(name="order-service"):
    return {
        "value": [
            {
                "name": name,
                "id": "abc",
                "defaultBranch": "refs/heads/main",
                "remoteUrl": f"https://acme@dev.azure.com/acme/Platform/_git/{name}",
            }
        ]
    }


def test_azure_fetch_org_flattens_projects():
    opener = FakeOpener([
        (has("_apis/projects"), ghp({"value": [{"id": "1", "name": "Platform"}, {"id": "2", "name": "Ops"}]})),
        (has("Platform/_apis/git/repositories"), ghp(_azure_repo_payload())),
        (has("Ops/_apis/git/repositories"), ghp(_azure_repo_payload(name="infra"))),
    ])
    out = fetch_repos("https://dev.azure.com/acme", opener=opener)
    assert out["provider"] == "azure-devops"
    names = [r["full_name"] for r in out["repos"]]
    assert names == ["Platform/order-service", "Ops/infra"]
    assert out["repos"][0]["default_branch"] == "main"
    assert out["repos"][0]["clone_url"].endswith("/_git/order-service")


def test_azure_fetch_project_direct():
    opener = FakeOpener([(has("My%20Project/_apis/git/repositories"), ghp(_azure_repo_payload()))])
    out = fetch_repos("https://dev.azure.com/acme/My%20Project", opener=opener)
    assert len(out["repos"]) == 1
    assert out["repos"][0]["full_name"] == "My Project/order-service"
    assert not any("_apis/projects" in u for u in opener.urls())


def test_azure_fetch_empty_org_raises():
    opener = FakeOpener([(has("_apis/projects"), ghp({"value": []}))])
    try:
        fetch_repos("https://dev.azure.com/acme", opener=opener)
        raise AssertionError("expected GitHostError")
    except GitHostError as e:
        assert e.status == 404


# ── Dispatch / link errors ─────────────────────────────────────────


def test_dispatch_unsupported_host():
    try:
        fetch_repos("https://example.com/acme")
        raise AssertionError("expected GitHostError")
    except GitHostError as e:
        assert e.status is None and "Unsupported link" in str(e)


def test_dispatch_invalid_link():
    try:
        fetch_repos("https://github.com/acme/repo")
        raise AssertionError("expected GitHostError")
    except GitHostError as e:
        assert "Unsupported link" in str(e)


def test_clone_urls_pass_project_validation():
    from engine.project_store import _validate_url

    opener = FakeOpener([(has("orgs/acme/repos"), ghp(_gh_org_payload()))])
    out = fetch_repos("https://github.com/acme", opener=opener)
    for r in out["repos"]:
        assert _validate_url(r["clone_url"]) == r["clone_url"]
