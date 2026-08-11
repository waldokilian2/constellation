# Requirements — Graph Diff & Compare ("what changed since the last scan")

> Branch: `feature/graph-diff` (rebuilt) · Status: draft · Base: `main`

## 1. Problem

Every rescan **silently overwrites** a project's `graph.json`. There is no way
to answer "what changed since the last analysis?" — entry points that appeared
or vanished, channels that were added or removed, call trees that grew or
shrank. For an evolving codebase this is the most-requested capability, and
all the data to compute it is already deterministic.

## 2. Goal (deterministic — no LLM)

Add per-project graph **snapshots**, a pure **`diff_graphs`** function, and a
**compare mode** in the UI, so a rescan can overlay exactly what changed onto
every view (galaxy, solar, path, flows, detail panel, project cards).

This stays true to the repo's core principle: **no LLM in the core**
(`AGENTS.md` → "Project Overview"). The engine's `diff_graphs` tool is the
single source of truth for *what* changed; the frontend only decides how it is
drawn.

## 3. Backend requirements

### 3.1 Pure diff tool — `engine/graph_tools.py`

- New pure function `diff_graphs(old: dict, new: dict) -> dict` (no I/O, no
  state — operates only on the graph dicts):
  - `entry_points`: `{added: [ids], removed: [ids], changed: [ids]}`
    (keyed by `id`).
  - `producers`: same shape (keyed by `id`).
  - `cross_repo_links`: `{added: [channels], removed: [channels]}` (keyed by
    `channel` — message topics/queues and HTTP path templates both count).
  - `summary`: counts per category (`entry_points_added` / `_removed` /
    `_changed`, `producers_*`, `links_added` / `links_removed`).
  - An entry point is `changed` when its `metrics` (depth/nodes/files)
    changed **or** its call-tree node set changed — compare
    `(method, file, line)` tuples via a depth-first walk.
  - Producers are `changed` when their serialized dict differs.
- Register the tool in **all** places (`AGENTS.md` → "Graph Tools" rule):
  1. `TOOL_DEFINITIONS` — name, description ("compare two graph snapshots…"),
     `parameters` with an `old_graph` object property.
  2. `execute_tool` dispatch table — `diff_graphs(args["old_graph"] or {}, graph)`.
  3. `_filter_args` — implicit via the parameter schema.
  4. `get_tool_definitions` — automatic (it returns `TOOL_DEFINITIONS`).
- Keep it deterministic and dependency-free; do not touch
  `EXTRACTED`/`INFERRED`/`AMBIGUOUS` semantics.

### 3.2 Snapshot persistence — `engine/project_store.py`

- Before `analyze_project` overwrites an existing `graph.json`, save a
  timestamped copy to `output/projects/<pid>/snapshots/<ts>.json`
  (`ts` = high-resolution epoch in the project metadata's format).
- Helpers:
  - `_save_snapshot(pid, graph)` — writes `snapshots/<ts>.json`, prunes to the
    last `SNAPSHOT_LIMIT = 10` (oldest removed).
  - `latest_snapshot(pid) -> dict | None`.
  - `list_snapshots(pid) -> [ts]` ascending.
  - `load_snapshot(pid, ts) -> dict | None` — **path-confined** to the
    snapshots dir (a crafted `ts` must not escape; see `engine/paths.py`
    conventions and `AGENTS.md` → Security).
- Persist the latest diff alongside the graph as `last_diff.json` so the
  project-list UI can show per-card change chips without recomputing.
- **Engine analysis runs in-process as today.** No subprocess isolation, no
  `win_accept_resilience` patch — those were earlier bundled ideas and are
  explicitly out of scope (see §9).

### 3.3 REST endpoints — `server.py`

- `GET /api/projects/{pid}/diff?at=<ts>&light=1`
  - `at` selects the snapshot to compare against (default: latest; unknown
    `at` → `404`).
  - `light=1` omits the two full graphs (cheap polling from the project list).
  - Response: `{diff, snapshots, compared_at, old_graph?, new_graph?}` where
    `diff` is the output of `diff_graphs` (single source of truth) and the
    graphs are returned so the UI can render before/after details.
- `GET /api/projects/{pid}/tools/diff` — GET convenience wrapper for the tool
  (mirrors the other GET tool wrappers).
- Snapshots are per-project; nothing global.

## 4. Frontend requirements (`web/src/app.jsx`)

### 4.1 Diff state (App)

- State: `diffInfo` (latest `/diff` payload for the active project),
  `compareInfo` (the comparison actually shown), `compareMode`,
  `compareTs`, `bannerDismissed`, `diffsByPid` (light payloads for cards).
- `fetchDiff(pid, at, light)` via `projPath(pid, "/diff…")`; `fetchProjectDiff`
  populates `diffsByPid` for project cards. Reset compare state when the
  active project changes or a rescan completes.
- Helper `diffStatus(compare)` → `{epStatus, chStatus, repoCounts, oldLinks,
  newLinks}` (pure presentation over the `/diff` payload); plus
  `diffSummaryText`, `diffHasChanges`, `fmtSnapshot`, `DIFF_LABELS`/`DIFF_COLORS`
  (green `added`, amber `changed`, red `removed`, dim `same`).

### 4.2 Compare bar

- Below the fixed header when the active project has snapshots: a banner line
  "Since last scan: [chips]" with a green **Compare** button (and a dismiss ×
  for the session).
- In compare mode the bar switches to "COMPARING current ↔ previous snapshot"
  with a snapshot `<select>` and an **Exit compare** button.
- The stage shrinks by the bar height while visible (`DIFF_BAR_H = 40`).

### 4.3 Compare overlays per view (green = added, amber = changed, red = removed)

- **Project cards**: "since last scan:" chip row using the light diff.
- **Galaxy**: per-repo rings + `+N/~N/−N` badges; message **and** HTTP edges
  recolor by channel status; unchanged edges dim; removed channels render as
  dashed **ghost edges**; the legend gains a "Since last scan" section.
- **Solar**: stars get `▲/~` badges; removed entry points render as dashed red
  **ghosts** with a toggle filter chip ("▼ N removed"); the view hint reads
  "… was N entry points before"; the docked channels panel shows a diff chip on
  each channel card.
- **Path**: call-tree nodes get solid-green / dashed-amber outlines; a removed
  strip lists old-only nodes (strikethrough); metrics deltas are surfaced.
- **Detail panel**: "Changes since last scan" block — entry point is new / this
  node is new / metrics `old → new` / "no changes".
- **Flows**: flow cards get status chips; flow edges recolor by channel status;
  repo nodes get diff rings.
- All overlays are **opt-in** (only while `compareMode`), driven by the `compare`
  prop; no behaviour change outside compare mode.

## 5. Styling (`web/src/styles.css`)

Port the diff styles: `.compare-bar` (+ `.active`, `.compare-text`,
`.compare-title`, `.compare-versions` select, `.compare-action`,
`.compare-x`, `.compare-inline`), `.diff-chip.added/changed/removed`,
galaxy `.repo-node.st-*` / `.repo-diff-badge` / edge `.st-*` + `.ghost-removed`,
legend diff rows, solar `.star.st-*` / `.star-badge` / `.star.ghost`, path
`.pv-node.st-*` / `.pv-diff-chip` / `.pv-removed-strip`, detail
`.dp-changes*`, flows `.flow-card.st-*` / `.flow-repo-node.st-*`,
`.pc-diff` / `.project-card.has-changes`. Reuse existing tokens; the diff
palette must match the app's existing success/warning/danger colors.

## 6. Tests

- `tests/test_graph_diff.py` (stdlib-only): added/removed/changed entry
  points; changed call-tree nodes; changed metrics; producer + link changes;
  unchanged graph → empty diff; tool registered in all required places;
  snapshot pruning keeps the last 10; store persists snapshot + diff.
- `tests/e2e/diff-compare.spec.js` (Playwright): open project → compare bar →
  enter compare → overlays appear → exit compare → state resets.

## 7. Acceptance criteria (summary)

- [ ] Rescan snapshots the previous graph and stores `last_diff.json`.
- [ ] `/api/projects/{pid}/diff` returns diff + snapshots (+ graphs unless
      `light=1`).
- [ ] Compare bar appears for projects with snapshots; snapshot picker works.
- [ ] Overlays render in galaxy/solar/path/detail/flows/project cards.
- [ ] `python tests/run_tests.py` passes; `npm run build` succeeds.

## 8. Implementation guidelines

- `diff_graphs` is pure and is the **only** source of truth for what changed.
- New tool must be registered in all four places (§3.1).
- Frontend diff helpers stay pure presentation (no engine logic duplicated).
- Match repo style: module docstrings, `# ── Section ──` separators, type
  hints. Stdlib-only for any new Python code.

## 9. Explicit exclusions (do NOT port)

- **Engine subprocess isolation** (`_analyze_subprocess`, engine run in a
  child process) — removed from scope by decision; analysis stays in-process.
- **`win_accept_resilience.py`** + its server import + `test_win_accept_resilience.py`
  — removed from scope by decision.
- The git-host import feature (separate — see
  `docs/requirements/git-project-import.md`).
- No new HTTP/LLM dependencies; no changes to `EXTRACTED`/`INFERRED`/
  `AMBIGUOUS` semantics; no full-history storage (snapshot cap of 10).
