# Task 03 — Blast-Radius Reachability & Mermaid Export

> Branch: `feature/blast-radius-export` · Status: open
> Issue: [#15](https://github.com/waldokilian2/constellation/issues/15) · Status tracked on the [Constellation board](https://github.com/users/waldokilian2/projects/1)

## Problem

Today's tracing is bounded by the per-entry-point **call tree** (depth 4,
`engine/call_graph.py:18` `MAX_DEPTH=4`). `trace_path`
(`engine/graph_tools.py:338`) only walks *within* a single entry point's tree.
There is no way to ask the two questions that matter for change impact:

1. **Blast radius** — "if I change repo A's contract (or one method), which
   downstream repos / entry points eventually break?" — across the inter-repo
   channel graph, not capped at depth 4.
2. **End-to-end flow export** — turn a discovered cross-repo chain into a
   pasteable artifact (Mermaid) for PRs/docs. Deterministic, zero AI.

## Goal (deterministic — no LLM)

Two new pure graph tools. They operate on the **inter-repo edges** already in
`cross_repo_links` (producer-id → consumer-id, plus the per-repo call trees),
so they reach further than the depth-limited call trees without re-parsing.

## Acceptance criteria

### A. `blast_radius(graph, start_repo, *, method="") -> dict`

- [ ] Build a directed **repo graph** from `cross_repo_links`
      (producer-repo → consumer-repo; reuse the id→repo extraction in
      `cross_repo.py:131` / `graph_tools.py:456`).
- [ ] With only `start_repo`: return all repos reachable downstream
      (`affected_repos`) + the entry points in them + the channels traversed.
- [ ] With `method` set: also seed from every entry point whose call tree
      contains that method (reuse `find_callers` walk at `graph_tools.py:297`),
      then expand across channel edges.
- [ ] Return shape:
      `{ start_repo, method, affected_repos: [...], affected_entry_points: [...],
         channels_traversed: [...], path: [[repo, channel, repo], ...] }`.
- [ ] Include a `depth` per affected repo (hop count) so the UI can tier the view.

### B. `export_mermaid(graph, *, scope) -> dict`

- [ ] `scope` selects what to render:
      - `{"kind": "channel", "channel": "order-events"}` → the producers/consumers
        of one channel (data from `get_channel_flow`).
      - `{"kind": "blast_radius", "start_repo": "..."}` → the graph from part A.
      - `{"kind": "overview"}` → all repos + channel edges (topology).
- [ ] Output a `mermaid` string (flowchart) + a markdown code fence, ready to paste
      into a PR/README. Keep node/edge styling minimal and consistent.
- [ ] Pure: no I/O; the caller (UI/REST/MCP) decides what to do with the string.

### C. Wiring

- [ ] Register **both** tools in all required places (`AGENTS.md` → "Graph Tools"):
      `TOOL_DEFINITIONS` (`graph_tools.py:26`), `execute_tool` (`:582`), GET
      routes in `server.py` (`:932`):
      `/api/projects/{pid}/tools/blast_radius?start_repo=X[&method=Y]`
      `/api/projects/{pid}/tools/mermaid?scope=...` (POST body for complex scope).
- [ ] Stdlib-only tests `tests/test_blast_radius.py` and
      `tests/test_mermaid_export.py` on hand-built graphs: a 3-repo chain, a
      diamond, and verify Mermaid output parses (contains `flowchart`, valid
      arrow syntax).

## Suggested anchors

- `engine/graph_tools.py:284` (`find_callers`), `:338` (`trace_path`),
  `:401` (`get_channel_flow`), `:456` (repo-from-id helper).
- `engine/cross_repo.py:118` (`find_repos_involved`) — repo-graph building blocks.
- `engine/models.py:111` (`CrossRepoLink`).

## Anti-goals

- Do **not** raise `MAX_DEPTH` in `call_graph.py` to fake reachability — reach
  across the channel graph instead.
- Do **not** add a graphviz/mermaid library dep. Mermaid text generation is
  trivial string building; keep the "no extra deps" convention (`AGENTS.md`).
- Do **not** invent edges that aren't in `cross_repo_links`.

## Conventions

Per `AGENTS.md`. Pure functions. `blast_radius` and `export_mermaid` must not
read files. Mermaid is plain string output — no third-party renderer needed at
analysis time (the browser/MCP consumer renders it).
