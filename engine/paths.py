"""
Cross-cutting source path resolution for the Constellation engine and API.

The engine stores *repo-relative* file paths in the graph so the output is
portable across mounts. This module turns a repo-relative (or legacy
absolute) path back into a real path on disk.

Reads are restricted to known repo roots. The graph records each repo's
absolute root at generation time (``repo_roots``), so resolution is both
portable and safe: a path is only accepted if it resolves inside a declared
root. This closes the arbitrary-file-read surface that existed when tools
passed raw paths straight to ``Path(...).read_text``.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional, Sequence


def _collect_paths(raw: object) -> list[Path]:
    if isinstance(raw, dict):
        values = list(raw.values())
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    elif isinstance(raw, str):
        values = [raw]
    else:
        values = []
    return [Path(v) for v in values if v]


def get_repo_roots(graph: dict) -> list[Path]:
    """Absolute repo roots declared in the graph (empty if none recorded)."""
    for key in ("repo_roots", "repo_root"):
        raw = graph.get(key)
        if raw:
            return [p.resolve() for p in _collect_paths(raw)]
    return []


def _repo_names(graph: dict) -> list[str]:
    raw = graph.get("repo_roots")
    if isinstance(raw, dict):
        return [str(n) for n in raw.keys() if n]
    return [str(r) for r in (graph.get("repos") or []) if r]


def _within_roots(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        rroot = root.resolve()
        if resolved == rroot or rroot in resolved.parents:
            return True
    return False


def _strip_repo_prefix(file_path: str, repo_names: Sequence[str]) -> Optional[str]:
    p = file_path.replace("\\", "/")
    for repo in repo_names:
        marker = f"/{repo}/"
        idx = p.find(marker)
        if idx != -1:
            return p[idx + len(marker):]
    return None


def resolve_source_path(
    graph: dict,
    file_path: str,
    fallback_roots: Optional[Sequence[Path]] = None,
) -> Optional[Path]:
    if not file_path:
        return None

    roots = get_repo_roots(graph) or list(fallback_roots or [])
    if not roots:
        return None

    repo_names = _repo_names(graph)

    tail = Path(file_path).name
    candidates: list[Path] = []

    for root in roots:
        rroot = Path(root)
        candidates.append(rroot / file_path)
        if tail:
            candidates.append(rroot / tail)
        stripped = _strip_repo_prefix(file_path, repo_names)
        if stripped and stripped != file_path:
            candidates.append(rroot / stripped)

    for cand in candidates:
        try:
            if cand.is_file() and _within_roots(cand, roots):
                return cand.resolve()
        except OSError:
            continue
    return None
