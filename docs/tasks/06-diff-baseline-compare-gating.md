# Task 06 — Diff Baseline Honesty + Compare-Pill Gating

> Branch: `fix/diff-baseline-compare-gating` (suggested — create from `docs/topology-layout-failure-analysis` or main)
> Status: in progress (implemented + verified, awaiting review) · Created: 2026-08-13
> Area: server (`server.py`) + engine (`engine/project_store.py`) + frontend (`web/src/app.jsx`) + tests (`tests/test_graph_diff.py`)
> Issue: none yet — brief-only handoff. If the task should be tracked on the
> [Constellation board](https://github.com/users/waldokilian2/projects/1),
> create an issue linking this file first (`gh issue create -p "Constellation"
> -l area/server -t "..." -b "Tracked brief: docs/tasks/06-diff-baseline-compare-gating.md"`).

## Context — the reported bug

A tester's project showed **"+32 entry points since last scan"** on the
constellation/project view, but inside the project the top-right pill rendered
an **unclickable "Up to date"** — no "| View changes" segment, no way to open
the diff. Both signals come from the same API payload and contradict each
other.

## Root cause (verified — do not re-derive, implement the fix)

1. **The diff API fabricates a diff when there is no baseline.**
   `server.py:1965` `project_diff()` does
   `old = PROJECT_STORE.latest_snapshot(pid)` then
   `execute_tool(graph, "diff_graphs", {"old_graph": old or {}})`.
   When the project has no snapshots, `old` is `None` and the diff runs
   against `{}` — `diff_graphs` (`engine/graph_tools.py:991-1044`) then
   reports **every** entry point, producer, and link as "added". Verified
   live: empty baseline vs the Java EE graph → `entry_points_added: 16` =
   its entire entry-point count. The tester's project had 32 → "+32".
   `/api/projects/{pid}/tools/diff` (`server.py:1957-1962`) has the same bug.
2. **A baseline only exists after the SECOND analysis.** Snapshots are
   written by `ProjectStore._persist_graph` (`engine/project_store.py:261-290`)
   only when a previous `graph.json` already existed (first persist writes no
   snapshot). Legacy-seeded projects bypass this entirely:
   `_seed_legacy_graph` (`:795`) writes `graph.json` directly. So fresh
   projects and seeded projects have `snapshots: []`.
3. **The pill gates compare mode on the wrong thing.**
   `ComparePill` (`web/src/app.jsx:481-518`):
   `hasHistory = snapshots.length > 0`, `canCompare = hasHistory`.
   With no snapshots the button is `disabled` and renders only the status
   segment ("Up to date") — correct-looking — while the **same payload's**
   fabricated `diff.summary` still drives the project-card "since last scan:
   +N" chips (`app.jsx:5150-5161`, fed by `fetchProjectDiff` → `?light=1`) and
   the pill's hover tooltip (`app.jsx:487,496`). Contradictory UI from one
   payload.
4. **The clean state is wrongly clickable.** Task 04 (compare-mode UX
   overhaul, `docs/tasks/04-compare-mode-ux-overhaul.md`) specified S1
   (snapshots exist, no changes) as **clickable → enter compare mode** with a
   "no changes" segment. That is being reversed by this task (decision below).

## Decisions (user-approved — do not re-litigate)

- **No baseline → suppress the diff entirely.** The server must NOT diff
  against `{}`. It returns `no_baseline: true` and `diff: null`; the UI hides
  every "since last scan" surface and the pill explains why it is disabled.
- **Remove the dead `last_diff.json` path.** `_persist_graph` writes it and
  `load_last_diff` (`project_store.py:250`) reads it, but nothing outside
  `tests/test_graph_diff.py:111-120` consumes it — the `/diff` endpoint
  recomputes live against the latest snapshot. Remove the write, the loader,
  and `last_diff_path`; update the test.
- **Compare mode requires changes.** `canCompare = hasHistory && hasChanges`.
  If there are no changes, the pill must NOT be clickable — only "View
  changes" opens compare mode. The passive "no changes" text stays visible
  for information.

## Required changes

### A. Server — `server.py`

`GET /api/projects/{pid}/diff` (`project_diff`, `server.py:1965`):

```python
old = None
if at:
    old = PROJECT_STORE.load_snapshot(pid, at)
    if old is None:
        raise HTTPException(status_code=404, detail=f"Snapshot '{at}' not found")
else:
    old = PROJECT_STORE.latest_snapshot(pid)

if old is None:
    return {
        "diff": None,
        "snapshots": PROJECT_STORE.list_snapshots(pid),
        "compared_at": "",
        "no_baseline": True,
    }

result = execute_tool(graph, "diff_graphs", {"old_graph": old})
payload = {
    "diff": result,
    "snapshots": PROJECT_STORE.list_snapshots(pid),
    "compared_at": old.get("generated_at", "") or "",
    "no_baseline": False,
}
# keep the existing `if not light:` branch (old_graph/new_graph) unchanged
```

`GET /api/projects/{pid}/tools/diff` (`tool_diff`, `server.py:1957`):

```python
old = PROJECT_STORE.latest_snapshot(pid)
if old is None:
    return {"diff": None, "no_baseline": True}
return execute_tool(graph, "diff_graphs", {"old_graph": old})
```

Do not change `diff_graphs` itself, its schema, or its registration.

### B. Engine — `engine/project_store.py`

`_persist_graph` (`:261-290`): delete the diff block — snapshot the previous
graph, write the new one, return nothing. `scan_project` (`:538`) already
ignores the return value, so no caller changes.

```python
def _persist_graph(self, pid: str, graph) -> None:
    """Snapshot the previous graph, then persist the new one.

    The diff itself is computed on demand by the /diff endpoint against the
    latest snapshot — no persisted diff file.
    """
    graph_dict = graph.to_dict() if hasattr(graph, "to_dict") else graph
    graph_path = self.graph_path(pid)
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    if graph_path.exists():
        try:
            old_graph = json.loads(graph_path.read_text())
        except (json.JSONDecodeError, OSError):
            old_graph = None
        if old_graph is not None:
            self._save_snapshot(pid, old_graph)
    _atomic_write_json(graph_path, graph_dict)
```

Delete `last_diff_path` (`:170`) and `load_last_diff` (`:250`). Check nothing
else references them (`tests/test_graph_diff.py` does — update it, part D).

### C. Frontend — `web/src/app.jsx`

1. **`ComparePill`** (`:481-518`). Replace the body with this logic:

```jsx
function ComparePill({ stale, generatedAt, diffLatest, comparing, snapshots, onToggleCompare }) {
  const hasHistory = !!(snapshots && snapshots.length > 0);
  const hasChanges = !!diffLatest && diffHasChanges(diffLatest);   // null-safe: diffHasChanges({diff:null}) === false
  const canCompare = hasHistory && hasChanges;                      // ← was hasHistory (S1 must not be clickable)
  const statusCls = stale ? "stale" : "ok";
  const statusLabel = stale ? "Stale" : "Up to date";
  const title = comparing ? "" :
    !hasHistory ? "No previous scan to compare against" :
    hasChanges ? (generatedAt ? "Last scanned: " + generatedAt + "\n" : "") + diffSummaryText(diffLatest) :
    "No changes since last scan";
  return (
    <div className="meta-right">
      <button
        type="button"
        className={"compare-pill status-" + statusCls + (comparing ? " comparing" : "") + (canCompare && !comparing ? " can-toggle" : "")}
        onClick={canCompare || comparing ? onToggleCompare : undefined}
        disabled={!canCompare && !comparing}
        aria-pressed={comparing}
        title={title}
      >
        <span className="seg seg-status">
          <span className="status-dot" />
          <span className="status-label">{comparing ? "COMPARING" : statusLabel}</span>
        </span>
        {hasHistory && !comparing && (           // was canCompare — S1 keeps the passive "no changes" text
          <span className={"seg seg-diff" + (hasChanges ? " st-diff" : "")}>
            <span className="seg-sep">|</span>
            {hasChanges ? (
              <span className="seg-action">View changes <span className="seg-arrow" aria-hidden="true">→</span></span>
            ) : ("no changes")}
          </span>
        )}
        {comparing && <span className="seg seg-exit">✕ exit</span>}
      </button>
    </div>
  );
}
```

Resulting states (git-staleness "Stale" stays an orthogonal overlay, unchanged):

| State | Condition | Render | Click |
|---|---|---|---|
| S0 no baseline | no snapshots | "Up to date", no diff segment, title "No previous scan to compare against" | disabled |
| S1 clean | snapshots, no changes | "Up to date \| no changes" (dim), title "No changes since last scan" | **disabled (new)** |
| S2 dirty | snapshots, changes | "Up to date \| View changes →" (amber pulse) | enabled → compare |
| S3 comparing | compareMode | "COMPARING · ✕ exit" | exit always enabled (even if a rescan drops changes to 0 mid-session) |

2. **Guard `enterCompare`** (`app.jsx:4628`-ish, inside the App component) as
   defense in depth — the pill is the only caller, but keep the invariant in
   one place:

```js
const enterCompare = () => {
  if (!diffInfo || !diffInfo.diff || !(diffInfo.snapshots || []).length || !diffHasChanges(diffInfo)) return;
  setCompareMode(true);
};
```

3. **No changes elsewhere.** Verify (no edits expected):
   - `diffHasChanges` (`:98-103`) and `diffSummaryText` (`:85-96`) are already
     null-safe on `diff: null`.
   - `compare` memo (`:4725-4728`) already returns `null` when `diffInfo.diff`
     is null, so no compare overlay can render decorations.
   - `ProjectCard` chips (`:5126-5160`): `changedCount` becomes 0 when the
     light payload has `diff: null`, so the "since last scan" chips disappear
     automatically — confirm, don't "fix".
   - `fetchDiff`/`fetchProjectDiff` (`:4560-4568`) need no signature changes.

### D. Tests — `tests/test_graph_diff.py` (stdlib only)

- Rewrite `test_store_persists_snapshot_and_diff` (`:105-120`) as
  `test_store_persists_snapshot_chain`:
  - first `_persist_graph` → `latest_snapshot(pid) is None`,
    `list_snapshots(pid) == []`, and no `last_diff.json` file exists under
    the project dir.
  - second `_persist_graph` with a different graph → exactly one snapshot,
    equal to the FIRST graph; `latest_snapshot(pid)` returns it.
  - `load_last_diff` and `last_diff_path` no longer exist (assert
    `not hasattr(ProjectStore, "load_last_diff")`).
- Keep all other tests. `diff_graphs` tests are unchanged (the pure tool is
  untouched).

## Acceptance criteria

- [x] `GET /api/projects/{pid}/diff` on a project with no snapshots returns `{"diff": null, "snapshots": [], "compared_at": "", "no_baseline": true}`; with a snapshot it returns today's shape plus `no_baseline: false`.
- [x] `GET /api/projects/{pid}/tools/diff` returns `{"diff": null, "no_baseline": true}` without a baseline; normal diff result otherwise.
- [x] `_persist_graph` no longer writes `last_diff.json`; `last_diff_path` and `load_last_diff` are deleted; no callers/tests reference them (except updated tests).
- [x] Compare pill: S0 disabled with explanatory title; S1 shows passive "no changes" and is NOT clickable; S2 shows "View changes →" and is clickable; S3 exit always works.
- [x] `enterCompare` is a no-op unless snapshots exist AND the diff has changes.
- [x] A project with no baseline shows zero "since last scan" chips anywhere (project cards, pill, tooltips).
- [x] `python tests/run_tests.py` passes except the 4 known pre-existing failures (`test_conversation_title`, 3× `test_mermaid_validator`).
- [x] `npm run build` passes (Windows: `npm.cmd run build`).

## Verification plan

Python: `.venv\Scripts\python.exe tests\run_tests.py` (Windows; the venv is
required for engine imports).

Server: `.venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8765`

Manual UI scenario:
1. Fresh project → first analysis → landing card shows NO diff chips; open
   project → pill reads "Up to date", disabled, title "No previous scan to
   compare against"; `curl /api/projects/{pid}/diff?light=1` shows
   `no_baseline: true, diff: null`.
2. Add a repo (or edit source) → rescan → snapshot exists, diff has real
   deltas → card chips appear, pill reads "Up to date | View changes →",
   clickable; compare mode renders badges/legend.
3. Rescan again with no source change → pill reads "Up to date | no
   changes" and is NOT clickable; if compare mode was open, `✕ exit` still
   works.
4. `npm run build` and confirm the served bundle matches (server serves
   `web/dist` from disk; compare the `index-*.js` hash on `/` vs `web/dist/assets`).

## Anti-goals

- Do NOT change `diff_graphs` itself, its `TOOL_DEFINITIONS` entry, or the
  `execute_tool` dispatch — the pure tool keeps `old_graph or {}` semantics
  for client-supplied calls; only the two REST endpoints gate on the baseline.
- Do NOT snapshot the initial scan as a baseline (decision: suppress).
- Do NOT touch git staleness ("Stale"), `/updates`, or the snapshot limit.
- Do NOT modify `web/src/galaxyLayout.js` or any layout code — separate work,
  just shipped on `docs/topology-layout-failure-analysis`.
- No new runtime dependencies; stdlib-only tests.
- Do NOT change `EXTRACTED`/`INFERRED`/`AMBIGUOUS` confidence semantics.

## Conventions & environment (for the implementing agent)

- Python: module docstrings, `from __future__ import annotations`, type
  hints, `# ── Section ──` separators (see `AGENTS.md` → "Conventions").
- Windows shell gotchas: use `npm.cmd` (plain `npm` breaks in PowerShell);
  use `.venv\Scripts\python.exe` for anything importing the engine.
- Pre-existing suite failures that are NOT this task: mermaid-validator
  (environmental) and conversation-title truncation — do not chase them.
- Do not commit unrelated files; do not push unless the user asks.
