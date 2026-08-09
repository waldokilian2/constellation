# Task 01 — Graph Diff & Versioning

> Branch: `feature/graph-diff` · Status: open
> Issue: [#13](https://github.com/waldokilian2/constellation/issues/13) · Status tracked on the [Constellation board](https://github.com/users/waldokilian2/projects/1)

## Problem

Every `rescan` **silently overwrites** the project's `graph.json`
(`engine/project_store.py:406`, `analyze_project`). There is no way to answer
"what changed since the last analysis?" — new/removed entry points, channels
that vanished, call trees that grew or shrank. For an *evolving* codebase this
is the single most-requested capability, and all the data to compute it is
already deterministic.

## Goal (deterministic — no LLM)

Add a pure `diff_graphs(old, new)` function plus snapshot persistence, so a
rescan can report what changed. This stays true to the repo's core principle:
**no LLM in the core** (`AGENTS.md` → "Project Overview").

## Acceptance criteria

- [ ] `ConstellationGraph.to_dict()` output is diffable (it already is — lists of dicts with stable `id` fields).
- [ ] New pure function in `engine/graph_tools.py`:
      `diff_graphs(old: dict, new: dict) -> dict` returning:
      - `entry_points`: `{added: [...ids], removed: [...ids], changed: [...ids]}`
      - `producers`: same shape
      - `cross_repo_links`: `added`/`removed` channels
      - `summary`: counts per category
      `changed` for an entry point = its `metrics` (depth/nodes/files) changed
      or its call-tree node set changed (compare `(method,file,line)` tuples).
- [ ] Snapshot persistence in `engine/project_store.py`:
      before overwriting at `:406`, if a previous `graph.json` exists, save a
      timestamped copy to `output/projects/<pid>/snapshots/<ts>.json`
      (keep last N, e.g. 10; oldest pruned).
- [ ] `analyze_project` (line `:369`) returns/stores the latest diff alongside
      the new graph (e.g. `output/projects/<pid>/last_diff.json`), so the UI can
      show "X new entry points, Y removed channels" without recomputing.
- [ ] Expose a new graph tool + REST route following the existing pattern:
      - register in `TOOL_DEFINITIONS` (`engine/graph_tools.py:26`),
        the `execute_tool` dispatch table (`:582`), and `_filter_args` (implicit).
      - GET convenience route in `server.py` next to the others (`:932` area):
        `/api/projects/{pid}/tools/diff`.
      - **Rule** (`AGENTS.md` → "Graph Tools"): a new tool must be registered in
        **all** of those places.
- [ ] A stdlib-only test at `tests/test_graph_diff.py` (discovered by
      `tests/run_tests.py`) covering: added/removed EPs, changed metrics,
      unchanged graph → empty diff.

## Suggested anchors

- `engine/models.py:140` — `ConstellationGraph` (data source).
- `engine/project_store.py:369` (`analyze_project`), `:406` (overwrite point).
- `engine/graph_tools.py:26` (`TOOL_DEFINITIONS`), `:577` (`execute_tool`).
- `server.py:932` (GET tool wrappers to mirror).

## Anti-goals

- Do **not** add an HTTP/LLM dependency (`AGENTS.md` → stdlib-only for new deps).
- Do **not** change `EXTRACTED`/`INFERRED`/`AMBIGUOUS` semantics.
- Do **not** store full historical graphs forever — cap snapshot count.

## Conventions

Follow `AGENTS.md` → "Conventions": module docstring, `from __future__ import
annotations`, type hints, `# ── Section ──` separators. Keep tools pure
(no I/O, no state) — `diff_graphs` must not read files.
