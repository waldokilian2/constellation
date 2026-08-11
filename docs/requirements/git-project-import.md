# Requirements — Git Project Import (universal git-host import)

> Branch: `feature/git-project-import` · Status: draft · Base: `main`

## 1. Problem

Creating a project currently means pasting repo URLs one at a time (or a
`local:` path). An organisation with a GitHub/GitLab/Bitbucket/Azure DevOps
account has to collect clone URLs manually before they can point Constellation
at them. The importer lets a user paste **one org/workspace/team-project link**
and pick the repositories to clone from a searchable list.

Importing many repos at once makes the ingest phase long (clone + scan each
repo). The progress UI must therefore stay visible and accurate the whole time,
otherwise the user cannot tell whether the import is stuck.

## 2. Goal

Let a user create a project from a git-host link:

1. Paste an org/workspace/team-project link (`https://github.com/acme`,
   `https://gitlab.com/group/subgroup`, `https://bitbucket.org/workspace`,
   `https://dev.azure.com/org/ProjectName`).
2. The backend resolves the link to the owner's **public** repositories.
3. The modal shows the repos with search + select-all + per-repo checkboxes.
4. "Create & import" clones the **checked** repos (shallow) and analyses them
   together, so cross-repo links are detected exactly as with URL import.

Deterministic, stdlib-only, no LLM involvement.

## 3. Backend requirements

### 3.1 `engine/git_hosts.py` (new module, stdlib only)

- Module docstring; `from __future__ import annotations`; type hints;
  `# ── Section ──` comment separators (repo convention).
- `class GitHostError(Exception)` with optional `status: int | None` mirroring
  the observed HTTP status (`None` for invalid links/network failures).
- **Pure link parsers** — `parse_link(url: str) -> dict | None` returning
  `{provider, owner, project?}`:
  - `github.com` → `/orgs|users/{owner}` (also accept bare
    `https://github.com/{owner}`).
  - `gitlab.com` → `/groups|/users/{path}` (nested groups allowed, e.g.
    `group/subgroup`).
  - `bitbucket.org` → `/workspace`.
  - `dev.azure.com` → `/org/ProjectName` (project optional; when absent, list
    projects first then flatten all repos across them).
  - Strict owner validation regexes per host so only a safe path segment can
    reach an API URL (see existing `_OWNER_RE`, `_GITLAB_PATH_RE`,
    `_AZURE_PROJECT_RE` in the reference implementation). Anything else → `None`.
- **Per-provider fetchers** — `fetch_github(owner, opener, token=None)`,
  `fetch_gitlab(owner, opener)`, `fetch_bitbucket(owner, opener)`,
  `fetch_azure(owner, project, opener)`. Each returns a list of normalised repo
  dicts: `{name, full_name, clone_url, description?}`.
  - Pagination via `link`/`next` headers (GitHub), `page` query params
    (GitLab), or `cursor` (Bitbucket); Azure uses the `/_apis/…` REST API.
  - Cap at `_MAX_REPOS = 500`; stop paging once reached.
- **Dispatch** — `fetch_repos(link, opener=None, token=None) -> dict` returning
  `{provider, owner, repos: [...]}`. Raises `GitHostError` for unsupported
  hosts, invalid links, 4xx/5xx, and network failures.
- **Injectable opener** — every fetcher takes an `opener` callable
  (`urllib.request.urlopen` by default, wrapped in `_DefaultOpener` so tests
  can inject fixture payloads offline). Never hard-code the network call inside
  a function that must be unit-tested.
- **Token support** — `github_token() -> str` returns `gh auth token` output or
  `$GITHUB_TOKEN` (empty when unavailable). Only GitHub uses a token; the
  others are unauthenticated public-API calls. Nothing else may touch env vars
  or subprocesses.
- Only each provider's **fixed API base** is ever contacted; the user link is
  used solely to extract a validated owner identifier (keep the
  arbitrary-network surface closed — see `AGENTS.md` → Security).

### 3.2 `server.py`

- `from engine import git_hosts` (place with the other engine imports).
- New route next to the project endpoints:

  ```
  GET /api/remotes/repos?link=<git-host link>
  ```

  - Empty/missing `link` → `400` with a clear detail.
  - `git_hosts.GitHostError` → `400` (or `e.status` when set) with the message.
  - Any other exception → `502 "Failed to reach the git host API"`.
  - Success → `{provider, owner, repos: [{name, full_name, clone_url, description?}]}`.
- No auth gate required on this endpoint (open local tool, consistent with the
  rest of the API when `CONSTELLATION_API_TOKEN` is unset).

## 4. Frontend requirements (`web/src/app.jsx`)

### 4.1 Ingestion modal — import mode (create mode only)

- Add an `importMode` state: `"urls" | "remote"` (default `"urls"`).
- **Tab bar** (two tabs, class `import-tab`, active state highlights cyan):
  "Git URLs" and "Import from a git host". Only rendered in create mode.
- **Remote tab** shows:
  - A link input (`remote-link`) + "Load repos" button. Button disabled while
    loading or when input empty; label "Loading…" while busy.
  - Error line on failure (reuse `.ingest-error`).
  - On success: **repo picker** —
    - Head: provider label (`GIT_HOST_LABELS`), owner, repo count.
    - Toolbar: search box (filters name + full_name + description,
      case-insensitive), "Select all (N)" checkbox, "X of N selected" readout.
    - List: one checkbox row per repo (`full_name` as the stable key),
      showing name, full name, description. Max height ~230px, scrollable.
    - All repos are selected by default on load (import-everything bias);
      project name auto-fills from the owner when empty.
- **Submit** — `repoUrls` must be the **selected repos' `clone_url`s**
  (in remote mode), passed to `POST /api/projects` exactly like URL import.
  Everything downstream (SSE stream, modal close on `done`, `onComplete`) is
  shared with URL import.

### 4.2 Progress bar visibility (CRITICAL — importing many repos)

The ingest phase for N repos can take a long time (N shallow clones + N scans).
Requirements:

1. **Always visible** — once the status is `running`/`done`/`error`, the
   progress + log strip must stay on screen regardless of where the user has
   scrolled in the modal (e.g. after picking 20 repos and scrolling the picker
   list). Implementation approach: move the `.ingest-log` block **out of the
   scrollable `.modal-body`** and render it as its own fixed strip between the
   body and the footer (`modal-card` is a flex column; give `.modal-body`
   `flex: 1 1 auto; min-height: 0` so it scrolls and the log strip is a
   `flex: none` child that can never scroll away). Do **not** rely on the
   element simply being at the bottom of the form.
2. **Accurate repo total** — the determinate progress denominator must be the
   number of repositories actually being imported: `repoUrls.length`
   (selected repos in remote mode), **not** the URL-textarea count (`validUrls`
   is empty in remote mode). Using the wrong total yields `NaN%` / a frozen
   bar.
3. **No divide-by-zero** — guard `computeIngestProgress` so a total of `0`
   falls back to an indeterminate bar instead of `0/0 = NaN`.
4. Log stream keeps auto-scrolling to the newest line (existing
   `logEndRef.scrollIntoView` behaviour).

## 5. Styling (`web/src/styles.css`)

Port the import styles (classes): `.import-mode-tabs`, `.import-tab` (+
`.active`, `:disabled`, `:hover`), `.remote-picker`,
`.remote-picker-head`, `.remote-provider`, `.remote-owner`, `.remote-count`,
`.remote-toolbar`, `.remote-search`, `.remote-select-all`, `.remote-selected`,
`.remote-list`, `.remote-row` (+ `.checked`, `:hover`), `.remote-row-name`,
`.remote-row-full`, `.remote-row-desc`, `.remote-empty`. Reuse existing design
tokens (`--border`, `--cyan`, `--panel`, `.text-input`, `.btn-ghost`,
`.btn-primary`, `.ingest-error`) — do not introduce a second visual language.

Plus the modal layout changes for §4.2 (`.modal-body` flex/scroll fix,
`.ingest-log` strip variant that stays put).

## 6. Tests

- `tests/test_git_hosts.py` (stdlib-only, discovered by `tests/run_tests.py`):
  - `parse_link`: valid + invalid links per host, URL-encoded chars rejected,
    nested GitLab groups, Azure with/without project.
  - `fetch_*`: fixture payloads via the injectable opener; pagination; missing
    fields tolerated; 404 → `GitHostError(status=404)`; unknown host → error;
    GitHub token header sent when a token is supplied; org fallback to user.
  - `fetch_repos` dispatch: `GitHostError` for unsupported hosts.
- `tests/e2e/git-host-import.spec.js` (Playwright, against the running server +
  mock host responses or a stub): open create-project modal → switch tab →
  paste link → load → select subset → create → progress visible throughout →
  project appears.

## 7. Acceptance criteria (summary)

- [ ] Pasting a supported git-host link lists its public repos.
- [ ] Search / select-all / per-repo selection work; only checked repos import.
- [ ] Create & import clones selected repos and analyses them together.
- [ ] Progress bar stays visible for the whole multi-repo import and shows the
      correct N-repo total (no `NaN%`, no hidden bar).
- [ ] New unit tests pass via `python tests/run_tests.py`; `npm run build`
      succeeds.

## 8. Implementation guidelines

- **Stdlib only** for HTTP (`urllib`) — adding `requests`/`httpx` is a
  regression (`AGENTS.md`).
- Keep parsers pure; keep fetchers testable via the injectable opener.
- No new runtime deps; no changes to the analysis engine, the graph format, or
  the MCP server.
- Match repo style: module docstrings, section separators, type hints.

## 9. Explicit exclusions (do NOT port)

- Graph diff & compare / snapshots (separate feature — see
  `docs/requirements/graph-diff.md`).
- Engine subprocess isolation and `win_accept_resilience.py` — these were
  bundled with an earlier draft of the diff feature and are explicitly out of
  scope for all new work.
