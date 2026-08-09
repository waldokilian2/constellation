# Task 02 — Orphan Detection & Repo Dependency Cycles

> Branch: `feature/orphan-cycle` · Status: open
> Issue: [#14](https://github.com/waldokilian2/constellation/issues/14) · Status tracked on the [Constellation board](https://github.com/users/waldokilian2/projects/1)

## Problem

The cross-repo linker already computes **both sides** of every channel
(`engine/cross_repo.py:43` builds the `channels` dict; `:64` keeps a link only
when there is ≥1 producer **and** ≥1 consumer). Two high-value findings fall out
of that data but are never surfaced:

1. **Orphan producers** — a producer emits to a channel nobody consumes
   (dead contract, misnamed queue, or a service not yet added to the project).
2. **Orphan consumers** — an entry point listens on a channel nobody produces to
   (dead listener / typo).
3. **Repo dependency cycles** — A → B → A through channels (architectural smell).

These are cheap to compute and exactly the kind of deterministic fact this tool
exists to find.

## Goal (deterministic — no LLM)

New pure functions that classify channels/edges, plus two graph tools to expose
them. No new parsing required — operate on the existing `cross_repo_links` +
`entry_points` + `producers` lists.

## Acceptance criteria

- [ ] `find_orphans(graph) -> dict` in `engine/graph_tools.py` returning:
      - `orphan_producers`: producer records whose `channel` has no consumer
        (compare against consumer entry points' `channel`; use the same consumer
        type set as `get_channel_flow` at `graph_tools.py:420`).
      - `orphan_consumers`: consumer entry points whose `channel` has no producer
        (exclude `HTTP_CALL` producers — those are resolved separately in
        `cross_repo.py:82`).
      - `summary`: counts.
      Note: a channel may be **legitimately** unconsumed within one project
      (e.g. boundary to an external system) — surface it, don't assert it's a bug.
- [ ] `find_cycles(graph) -> dict` returning repo-level dependency cycles via the
      channel edges. Build a directed repo graph:
      producer-repo → consumer-repo for each `cross_repo_link`
      (repo extracted from IDs as in `cross_repo.py:131` and
      `graph_tools.py:456`). Detect simple cycles (DFS). Return each cycle as an
      ordered list of repo names + the channels involved.
- [ ] Register **both** as graph tools in **all** required places
      (`AGENTS.md` → "Graph Tools"):
      `TOOL_DEFINITIONS` (`graph_tools.py:26`), `execute_tool` dispatch (`:582`),
      GET convenience routes in `server.py` (`:932` area):
      `/api/projects/{pid}/tools/orphans`, `/api/projects/{pid}/tools/cycles`.
- [ ] (Optional, UI) In the galaxy view (`web/app.js`), flag orphan channels with
      a distinct edge style. Out of scope if you want to keep this task engine-only.
- [ ] Stdlib-only test `tests/test_orphans_cycles.py` using a hand-built graph
      dict (no engine run needed): one orphan producer, one orphan consumer,
      one A→B→A cycle.

## Suggested anchors

- `engine/cross_repo.py:43` (channel index), `:64` (link condition), `:72`
  (http pass — orphans must not confuse message vs http channels).
- `engine/graph_tools.py:401` (`get_channel_flow`), `:445` (`list_channels`) —
  reuse their repo-extraction helpers.
- `engine/models.py:111` (`CrossRepoLink`), `:86` (`Producer`).

## Anti-goals

- Do **not** mutate `cross_repo_links` — these are *analytical views* over the
  graph, pure functions only.
- Do **not** treat every orphan as an error; return facts, let the UI/AI judge.
- Do **not** change the message-vs-http channel matching in `cross_repo.py`.

## Conventions

Per `AGENTS.md` → "Conventions". Pure functions (no I/O). Keep `get_channel_flow`
and `list_channels` as the style reference for these two new tools.
