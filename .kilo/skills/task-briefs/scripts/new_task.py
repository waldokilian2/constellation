#!/usr/bin/env python3
"""
Scaffold a new Constellation task: write a repo brief, open a GitHub issue,
add it to the Projects board, and set it to "Todo".

Usage:
    python new_task.py "Title of the task" --area engine
    python new_task.py "Add X" --slug add-x --area web --no-issue

Stdlib-only (matches the repo's no-extra-deps convention). Requires the `gh`
(GitHub CLI) to be authenticated.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
TEMPLATE = SKILL_DIR.parent / "assets" / "brief-template.md"

DEFAULT_OWNER = "waldokilian2"
DEFAULT_PROJECT = "Constellation"


# ── Helpers ───────────────────────────────────────────────────────

def repo_root() -> Path:
    """Walk up to the directory containing AGENTS.md (fall back to cwd)."""
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "AGENTS.md").exists():
            return p
    return here


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "task"


def next_number(tasks_dir: Path) -> int:
    nums = []
    for f in tasks_dir.glob("[0-9][0-9]-*.md"):
        try:
            nums.append(int(f.name[:2]))
        except ValueError:
            pass
    return (max(nums) + 1) if nums else 1


def gh(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], text=True,
                          capture_output=True, check=True)
    return proc.stdout.strip()


# ── GitHub wiring ─────────────────────────────────────────────────

def create_issue(title: str, brief_rel: str, area: str, project: str) -> str:
    body = (
        f"Tracked brief (full spec, acceptance criteria, file anchors, test to write):\n"
        f"{brief_rel}\n\n"
        f"Follow AGENTS.md conventions. Keep new graph tools pure (no I/O) and register "
        f"them in all required places (TOOL_DEFINITIONS, execute_tool dispatch, REST route)."
    )
    return gh(["issue", "create", "-p", project,
               "-l", f"area/{area}", "-l", "enhancement",
               "-t", title, "-b", body])


def set_todo(owner: str, project: str, issue_url: str) -> None:
    """Best-effort: move the freshly created issue to the board's 'Todo' column."""
    try:
        num = gh(["project", "list", "--owner", owner, "--format", "json",
                  "--jq", f'.[] | select(.name=="{project}") | .number'])
        if not num:
            return
        proj = num.splitlines()[0]
        field_id = gh(["project", "field-list", proj, "--owner", owner,
                       "--format", "json",
                       "--jq", '.fields[] | select(.name=="Status") | .id'])
        todo_id = gh(["project", "field-list", proj, "--owner", owner,
                      "--format", "json",
                      "--jq", '.fields[] | select(.name=="Status") | .options[] | select(.name=="Todo") | .id'])
        proj_id = gh(["project", "view", proj, "--owner", owner,
                      "--format", "json", "--jq", ".id"])
        issue_num = issue_url.rstrip("/").split("/")[-1]
        iid = gh(["project", "item-list", proj, "--owner", owner,
                  "--format", "json",
                  "--jq", f".items[] | select(.content.number == {issue_num}) | .id"])
        if field_id and todo_id and proj_id and iid:
            gh(["project", "item-edit", "--id", iid, "--field-id", field_id,
                "--project-id", proj_id, "--single-select-option-id", todo_id])
    except subprocess.CalledProcessError:
        pass


# ── Main ──────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a Constellation task.")
    ap.add_argument("title", help="Task title (issue title + brief heading)")
    ap.add_argument("--slug", help="URL/branch slug (default: derived from title)")
    ap.add_argument("--area", default="engine",
                    choices=["engine", "web", "server", "mcp"],
                    help="Area label (default: engine)")
    ap.add_argument("--owner", default=DEFAULT_OWNER, help="GitHub owner")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="Project title")
    ap.add_argument("--no-issue", action="store_true",
                    help="Write only the brief; do not call gh")
    args = ap.parse_args()

    root = repo_root()
    tasks_dir = root / "docs" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    number = next_number(tasks_dir)
    slug = args.slug or slugify(args.title)
    branch = f"feature/{slug}"
    fname = f"{number:02d}-{slug}.md"
    brief_path = tasks_dir / fname
    if brief_path.exists():
        print(f"error: {brief_path} already exists", file=sys.stderr)
        return 1

    brief_rel = f"docs/tasks/{fname}"
    issue_md = "(not linked)"
    issue_url = ""

    if not args.no_issue:
        try:
            issue_url = create_issue(args.title, brief_rel, args.area, args.project)
            num = issue_url.rstrip("/").split("/")[-1]
            issue_md = f"[#{num}]({issue_url})"
        except subprocess.CalledProcessError as e:
            print(f"warning: gh issue create failed: {e.stderr.strip()}", file=sys.stderr)
            print("warning: brief written without an issue link.", file=sys.stderr)

    content = TEMPLATE.read_text()
    content = (content.replace("{{NUMBER}}", str(number))
                       .replace("{{TITLE}}", args.title)
                       .replace("{{BRANCH}}", branch)
                       .replace("{{DATE}}", date.today().isoformat())
                       .replace("{{ISSUE}}", issue_md))
    brief_path.write_text(content)

    if issue_url and not args.no_issue:
        set_todo(args.owner, args.project, issue_url)

    print(f"Brief : {brief_path}")
    if issue_url:
        print(f"Issue : {issue_url}")
        print(f"Board : https://github.com/users/{args.owner}/projects")
    print("Fill in Problem / Goal / Acceptance criteria / Suggested anchors, then share.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
