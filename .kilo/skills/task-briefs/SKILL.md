---
name: task-briefs
description: Constellation's task-tracking workflow — repo briefs in docs/tasks/ paired with GitHub issues as cards on a GitHub Projects v2 Kanban board. Use when creating, updating, claiming, or completing a tracked task; scaffolding a new task; moving an issue across board columns; or when a dev or AI agent needs the conventions for picking up and shipping a task. Covers the brief format, the issue↔brief↔board relationship, the status lifecycle, and the engine rules (no LLM in core, pure graph tools, register in all required places).
---

# Task Briefs — Constellation Work Tracking

## The three layers

1. **Brief** (`docs/tasks/NN-slug.md`) — the deep, AI-readable spec: problem, acceptance
   criteria, exact file anchors, the test to write, anti-goals, conventions. Checked into
   git so it versions with the code.
2. **Issue** (GitHub) — the lightweight card. Title + one-paragraph summary + a link to the
   brief. Labeled `area/*`. This is what humans glance at and what moves on the board.
3. **Board** (GitHub Projects v2 "Constellation") — the Kanban view over issues.
   Columns: **Todo → In Progress → In Review → Done**.

The brief and issue are 1:1. The issue body links to the brief; the brief header links to
the issue. Closing the issue (on PR merge) is the source of truth for "Done".

## When to use this skill

- Creating a new tracked task
- Starting, updating, or finishing a task
- Moving a task across board columns
- Onboarding a dev or AI agent to how work is tracked here

## Where things live

- Briefs: `docs/tasks/NN-slug.md` (`NN` = zero-padded, assigned in order)
- Cards: GitHub issues on `waldokilian2/constellation`
- Board: https://github.com/users/waldokilian2/projects/1 ("Constellation")
- Tooling: this skill's `scripts/new_task.py`

## Add a new task

Run (from the repo root):

    python .kilo/skills/task-briefs/scripts/new_task.py "<Title>" --area <engine|web|server|mcp>

This writes the brief, opens the issue, adds it to the board as **Todo**, and cross-links
them. Pass `--slug <name>` to control the branch slug. Then edit the brief to fill in
Problem, Goal, Acceptance criteria, Suggested anchors, Anti-goals before sharing.

Manual alternative: copy `assets/brief-template.md` → `docs/tasks/NN-slug.md`, fill it in,
then `gh issue create -p "Constellation" -l area/<x> -l enhancement -t "<Title>" -b "Tracked
brief: docs/tasks/NN-slug.md"`, and paste the issue URL back into the brief header.

## Status lifecycle & board columns

- **Todo** — specced, unclaimed
- **In Progress** — someone is working it
- **In Review** — PR open, awaiting review
- **Done** — merged; issue closed

Move a card with `gh project item-edit` (Status field) or by dragging it on the board. Keep
the brief's `Status:` line in sync. Closing the issue marks it done.

## Start working on a task

1. Read the **brief** end to end, then `AGENTS.md`.
2. Branch from the brief's `Branch:` field.
3. Implement against the acceptance-criteria checkboxes.
4. Write the test named in the brief; run `python tests/run_tests.py`.
5. Do not violate the brief's Anti-goals or change `EXTRACTED`/`INFERRED`/`AMBIGUOUS`
   semantics (AGENTS.md → "Confidence Tags").

## Finish a task

- Tick every acceptance-criteria box in the brief.
- Move the card to **In Review**, open the PR, then to **Done** on merge.
- The issue closes with the PR; the brief stays in git as the record.

## Rules for AI agents working a task

- Treat the graph data as fact; **never re-derive structure with an LLM**
  (AGENTS.md → "Project Overview"). AI is advisory only.
- Keep new graph tools **pure** (no I/O, no state) and register them in **all** required
  places: `TOOL_DEFINITIONS`, `execute_tool` dispatch, `_filter_args`, and the REST route
  (AGENTS.md → "Graph Tools").
- Add **no new runtime dependencies** — stdlib where possible (AGENTS.md).
- Write the stdlib test specified in the brief; the suite is `python tests/run_tests.py`.
- Keep source reads confined to recorded `repo_roots` — do not open arbitrary-file-read
  surfaces (AGENTS.md → "Security").

## Tooling

- `scripts/new_task.py` — scaffold brief + issue + board card in one command
  (see "Add a new task").
- `gh` drives everything else. **No MCP server is required** — the agent calls `gh`
  directly. (A GitHub MCP server can be added later only if other AI agents without shell
  access need to manipulate the board.)
