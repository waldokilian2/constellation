# Task {{NUMBER}} — {{TITLE}}

> Branch: `{{BRANCH}}` · Status: open
> Created: {{DATE}}
> Issue: {{ISSUE}}

## Problem

<!-- 1-3 sentences. What is missing or broken today? Reference the current behavior. -->

## Goal

<!-- What this task delivers. Keep it deterministic where possible (no LLM in the core). -->

## Acceptance criteria

- [ ] <!-- concrete, checkable item -->
- [ ] 

## Suggested anchors

<!-- file:line references into the current codebase, e.g. engine/graph_tools.py:577 -->

## Anti-goals

- <!-- what NOT to touch / scope to avoid -->

## Conventions

Follow `AGENTS.md` → "Conventions". Keep new graph tools pure (no I/O) and register
them in all required places (`TOOL_DEFINITIONS`, `execute_tool` dispatch, `_filter_args`,
REST route). No new runtime deps. Write the stdlib test and run `python tests/run_tests.py`.
