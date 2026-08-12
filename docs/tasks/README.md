# Constellation Tasks

Tracked work for the Constellation engine. Each task has a **brief** here (the deep spec)
and a **GitHub issue** card on the [Constellation board](https://github.com/users/waldokilian2/projects/1)
(Todo → In Progress → In Review → Done).

## Open tasks

| # | Title | Brief | Area |
|---|-------|-------|------|
| [#13](https://github.com/waldokilian2/constellation/issues/13) | Graph diff & versioning | [01-graph-diff-versioning.md](01-graph-diff-versioning.md) | engine |
| [#14](https://github.com/waldokilian2/constellation/issues/14) | Orphan + cycle detection | [02-orphan-cycle-detection.md](02-orphan-cycle-detection.md) | engine |
| [#15](https://github.com/waldokilian2/constellation/issues/15) | Blast-radius + Mermaid export | [03-blast-radius-mermaid-export.md](03-blast-radius-mermaid-export.md) | engine |
| — | Compare-mode UX overhaul (Topology & Orbs) | [04-compare-mode-ux-overhaul.md](04-compare-mode-ux-overhaul.md) | frontend |

## Workflow

See the **task-briefs** skill (`.kilo/skills/task-briefs/SKILL.md`). TL;DR:

- **Add a task:** `python .kilo/skills/task-briefs/scripts/new_task.py "Title" --area engine`
- **Start a task:** read the brief + `AGENTS.md`, branch, implement, run `python tests/run_tests.py`.
- **Status:** move the issue on the [board](https://github.com/users/waldokilian2/projects/1).
