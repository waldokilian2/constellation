"""
Project store — multi-project persistence for Constellation.

A *project* is a named collection of repos that the engine analyses
together as one graph. This module owns:

  * the ``projects.json`` index (list of project metadata),
  * per-project directories (``output/projects/<id>/``) holding each
    project's ``graph.json`` and a ``repos/`` clone root,
  * git-clone ingestion of repo sources,
  * running the deterministic engine over a project's repos while
    streaming its progress logs to a callback (used by the SSE
    ingestion endpoints).

The engine itself stays pure and unchanged: it only knows about
``repo_dirs``. This store is the glue that maps git URLs → cloned paths
→ engine input → persisted graph + metadata.

Paths are kept repo-relative inside the graph (see ``engine/paths.py``);
the project's ``repo_roots`` point at the clone root so source reads
stay confined.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional
import hashlib
import json
import re
import shutil
import subprocess
import sys


# ── Allowed git URL schemes ────────────────────────────────────────
# Restrict to https/http to avoid file:// reads and to keep credential
# injection surfaces small. ssh:// and git@host:path are rejected for now
# (no SSH agent forwarding in the local tool).
_ALLOWED_SCHEMES = ("https://", "http://")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str, fallback: str = "project") -> str:
    """Turn a human name into a filesystem/url-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name or "").strip("-").lower()
    return slug or fallback


def _safe_repo_dirname(name: str) -> str:
    """Filesystem-safe directory name for a cloned repo."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", name or "").strip("-").lower()
    return cleaned or "repo"


def repo_name_from_url(url: str) -> str:
    """Derive a repo name from a git URL (``…/order-service.git`` → ``order-service``)."""
    cleaned = url.strip().rstrip("/")
    cleaned = re.sub(r"\.git$", "", cleaned)
    last = cleaned.split("/")[-1] or cleaned.split(":")[-1]
    return last or "repo"


def _validate_url(url: str) -> str:
    """Accept an https/http git URL, rejecting anything dangerous.

    Returns the trimmed URL. Raises ``ValueError`` for disallowed schemes
    or URLs that look like CLI-flag injection.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Empty repository URL")
    if not url.startswith(_ALLOWED_SCHEMES):
        raise ValueError(
            "Only https:// and http:// git URLs are supported in this build."
        )
    # No leading dashes on any path segment (guards against arg injection).
    for part in url.split("/"):
        if part.startswith("-"):
            raise ValueError("Invalid URL segment")
    return url


class _StreamTee:
    """Wraps stdout, forwarding whole lines to a sink callable in real time.

    The engine communicates progress via ``print(...)``, so this lets the
    ingestion endpoints stream ``[scan]`` / ``[link]`` / ``[done]`` lines
    live without changing the engine.
    """

    def __init__(self, real, sink: Callable[[str], None]):
        self.real = real
        self.sink = sink
        self._buf = ""

    def write(self, s: str) -> int:
        self.real.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.sink(line)
        return len(s)

    def flush(self) -> None:
        self.real.flush()
        if self._buf.strip():
            self.sink(self._buf)
        self._buf = ""

    def __getattr__(self, item):
        # Delegate anything else (isatty, encoding, …) to the real stream.
        return getattr(self.real, item)


# ── ProjectStore ───────────────────────────────────────────────────


class ProjectStore:
    """Owns the on-disk project index and per-project artefacts.

    All paths are derived from ``base_dir`` (the repo root), so the store
    writes under ``<base_dir>/output/projects``.
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"
        self.projects_dir = self.output_dir / "projects"
        self.index_file = self.output_dir / "projects.json"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    # ── paths ──────────────────────────────────────────────────────

    def project_dir(self, pid: str) -> Path:
        return self.projects_dir / pid

    def graph_path(self, pid: str) -> Path:
        return self.project_dir(pid) / "graph.json"

    def repos_dir(self, pid: str) -> Path:
        return self.project_dir(pid) / "repos"

    # ── index I/O ──────────────────────────────────────────────────

    def _read_index(self) -> list[dict]:
        if not self.index_file.exists():
            return []
        try:
            data = json.loads(self.index_file.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _write_index(self, projects: list[dict]) -> None:
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        self.index_file.write_text(json.dumps(projects, indent=2))

    # ── queries ────────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        return sorted(self._read_index(), key=lambda p: p.get("updated_at", ""), reverse=True)

    def get_project(self, pid: str) -> Optional[dict]:
        for p in self._read_index():
            if p.get("id") == pid:
                return p
        return None

    def load_graph(self, pid: str) -> dict:
        """Load a project's graph.json, raising FileNotFoundError if absent."""
        path = self.graph_path(pid)
        if not path.exists():
            raise FileNotFoundError(f"Project '{pid}' has no graph yet")
        return json.loads(path.read_text())

    # ── mutation ───────────────────────────────────────────────────

    def _new_id(self, name: str) -> str:
        slug = _slugify(name)
        stamp = hashlib.sha1(f"{name}-{_now()}-constellation".encode()).hexdigest()[:5]
        return f"{slug}-{stamp}"

    def _unique_repo_name(self, pid: str, name: str) -> str:
        """Disambiguate a repo name within a project (``order-service`` → ``order-service-2``)."""
        existing = {
            (r or {}).get("name") for r in (self.get_project(pid) or {}).get("repos", [])
        }
        candidate = name
        n = 2
        while candidate in existing:
            candidate = f"{name}-{n}"
            n += 1
        return candidate

    def create_meta(self, name: str) -> dict:
        """Create a project metadata record (status ``analyzing``) and persist it.

        The project is created up-front so it appears in the list immediately;
        ``ingest`` / ``analyze_project`` fill in the graph and stats later.
        """
        pid = self._new_id(name)
        pdir = self.project_dir(pid)
        pdir.mkdir(parents=True, exist_ok=True)
        self.repos_dir(pid).mkdir(parents=True, exist_ok=True)
        meta = {
            "id": pid,
            "name": (name or "Untitled Project").strip() or "Untitled Project",
            "created_at": _now(),
            "updated_at": _now(),
            "repos": [],
            "stats": {},
            "status": "analyzing",
        }
        self._upsert(meta)
        return meta

    def _upsert(self, meta: dict) -> None:
        projects = self._read_index()
        pid = meta["id"]
        for i, p in enumerate(projects):
            if p.get("id") == pid:
                projects[i] = meta
                break
        else:
            projects.append(meta)
        self._write_index(projects)

    def delete(self, pid: str) -> bool:
        projects = self._read_index()
        remaining = [p for p in projects if p.get("id") != pid]
        if len(remaining) == len(projects):
            return False
        self._write_index(remaining)
        pdir = self.project_dir(pid)
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)
        return True

    # ── ingestion ──────────────────────────────────────────────────

    def clone_repo(
        self,
        pid: str,
        url: str,
        log: Callable[[str], None] = lambda _msg: None,
    ) -> dict:
        """Clone a single git URL into the project's ``repos/`` dir.

        Returns the repo source record ``{name, source, path}``.
        Raises ``RuntimeError`` if the clone fails.
        """
        url = _validate_url(url)
        repo_name = self._unique_repo_name(pid, repo_name_from_url(url))
        dest = self.repos_dir(pid) / _safe_repo_dirname(repo_name)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)

        log(f"[clone] Cloning {repo_name} ← {url}")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(dest)],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or "").strip().splitlines()
            tail = err[-1] if err else f"git exited {e.returncode}"
            raise RuntimeError(f"Clone failed for {repo_name}: {tail}") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Clone timed out for {repo_name}") from None

        log(f"[clone] {repo_name} ready at {dest}")
        return {"name": repo_name, "source": url, "path": str(dest)}

    def analyze_project(
        self,
        pid: str,
        log: Callable[[str], None] = lambda _msg: None,
    ) -> dict:
        """Run the engine over the project's current repos and persist the graph.

        Streams the engine's progress lines to ``log`` as they are printed.
        Updates and returns the project metadata (stats + status + updated_at).
        Raises ``ValueError`` if the project has no repos to analyse.
        """
        meta = self.get_project(pid)
        if not meta:
            raise ValueError(f"Unknown project: {pid}")

        repo_dirs = [
            (r["name"], Path(r["path"]))
            for r in meta.get("repos", [])
            if r.get("path") and Path(r["path"]).exists()
        ]
        if not repo_dirs:
            meta["status"] = "error"
            meta["updated_at"] = _now()
            self._upsert(meta)
            raise ValueError("No cloned repos available to analyse")

        from .constellation import ConstellationEngine  # local import keeps store light

        def _run():
            engine = ConstellationEngine()
            graph = engine.analyze(repo_dirs)
            return graph

        graph = _capture_stdout(_run, log)

        # Persist graph + stats.
        self.graph_path(pid).parent.mkdir(parents=True, exist_ok=True)
        self.graph_path(pid).write_text(graph.to_json())

        meta["stats"] = {
            "repos": len(repo_dirs),
            "entry_points": len(graph.entry_points),
            "producers": len(graph.producers),
            "cross_repo_links": len(graph.cross_repo_links),
        }
        meta["status"] = "ready"
        meta["updated_at"] = _now()
        self._upsert(meta)
        return meta

    def ingest(
        self,
        pid: str,
        urls: list[str],
        log: Callable[[str], None] = lambda _msg: None,
    ) -> dict:
        """Clone the given URLs into an existing project, then re-analyse it.

        Used by both the "create project" and "add repo" flows. Returns the
        updated project metadata. Failed clones are skipped (logged); analysis
        still runs over whatever cloned successfully.
        """
        for url in urls:
            try:
                record = self.clone_repo(pid, url, log=log)
                self._append_repo(pid, record)
            except (RuntimeError, ValueError) as e:
                log(f"[clone] {e}")

        return self.analyze_project(pid, log=log)

    def _append_repo(self, pid: str, record: dict) -> None:
        meta = self.get_project(pid)
        if not meta:
            return
        meta.setdefault("repos", []).append(record)
        meta["updated_at"] = _now()
        self._upsert(meta)

    def mark_status(self, pid: str, status: str, message: str = "") -> None:
        """Set a project's status (``analyzing`` / ``ready`` / ``error``)."""
        meta = self.get_project(pid)
        if not meta:
            return
        meta["status"] = status
        if message:
            meta["error"] = message
        elif status != "error":
            meta.pop("error", None)
        meta["updated_at"] = _now()
        self._upsert(meta)

    def mark_error(self, pid: str, message: str = "") -> None:
        """Flag a project's status as ``error`` (used by the streaming ingest path)."""
        self.mark_status(pid, "error", message)

    # ── legacy seeding ─────────────────────────────────────────────

    def ensure_legacy_seed(self) -> None:
        """Import a pre-multi-project ``output/graph.json`` as a "Default" project.

        Keeps the existing demo/start.sh workflow working on first load: the
        seeded project points at the legacy graph's repo_roots (local test
        repos), so source reads keep resolving. Runs only when the index is
        empty and a legacy graph is present.
        """
        legacy = self.output_dir / "graph.json"
        if not legacy.exists() or self.list_projects():
            return

        try:
            graph = json.loads(legacy.read_text())
        except (json.JSONDecodeError, OSError):
            return

        repo_roots = graph.get("repo_roots") or {}
        repos = [
            {"name": name, "source": f"local:{path}", "path": path}
            for name, path in repo_roots.items()
        ]
        pid = self._new_id("Default")
        pdir = self.project_dir(pid)
        pdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, self.graph_path(pid))

        meta = {
            "id": pid,
            "name": "Default",
            "created_at": graph.get("generated_at") or _now(),
            "updated_at": _now(),
            "repos": repos,
            "stats": {
                "repos": len(repos),
                "entry_points": len(graph.get("entry_points", [])),
                "producers": len(graph.get("producers", [])),
                "cross_repo_links": len(graph.get("cross_repo_links", [])),
            },
            "status": "ready",
            "seeded": True,
        }
        self._upsert(meta)


# ── helpers ────────────────────────────────────────────────────────


def _capture_stdout(fn: Callable[[], object], sink: Callable[[str], None]):
    """Run ``fn`` while teeing its stdout lines to ``sink`` in real time."""
    real = sys.stdout
    tee = _StreamTee(real, sink)
    sys.stdout = tee
    try:
        return fn()
    finally:
        sys.stdout = real
        tee.flush()
