"""
Constellation Engine — main orchestrator.

Given one or more source directories, this engine:
1. Detects all entry points (message handlers, REST endpoints, event listeners)
2. Detects all message producers
3. Builds call trees for each entry point
4. Links repos via shared message channels
5. Outputs a single graph.json

Usage:
    python -m engine.constellation /path/to/repo1 /path/to/repo2 --output graph.json
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import argparse
import sys

from .entry_detector import EntryPointDetector
from .call_graph import CallGraphBuilder, _is_trivial_definition
from .cross_repo import CrossRepoLinker
from .symbol_index import SymbolIndex
from .languages import java_ast
from .models import ConstellationGraph, CallNode, ConfidenceTag


def _is_test_file(path: Path) -> bool:
    """Standard Maven/Gradle test detection (src/test/, *Test/*Tests/*IT)."""
    s = str(path).replace("\\", "/")
    if "/src/test/" in s or "/test/" in s:
        return True
    stem = path.stem
    return stem.endswith("Test") or stem.endswith("Tests") or stem.endswith("IT")


def _walk_java_files(repo_path: Path):
    """Yield every non-test ``*.java`` under ``repo_path``, skipping build output.

    Uses a pruned ``os.walk`` (shared with config discovery) instead of
    ``rglob`` so that (a) Maven `target/` output is never descended into —
    except ``target/generated-sources``, which holds real analyzed code — and
    (b) a directory vanishing mid-walk or a path Windows refuses to stat
    skips that subtree instead of killing the whole analysis (deep Liberty
    `workarea` trees inside target make naive rglobs raise FileNotFoundError).
    """
    import os
    from .symbol_index import prune_dirnames
    for dirpath, dirnames, filenames in os.walk(repo_path, topdown=True, onerror=None):
        prune_dirnames(dirpath, dirnames)
        for fn in sorted(filenames):
            if fn.endswith(".java"):
                p = Path(dirpath) / fn
                if not _is_test_file(p):
                    yield p


def _progress_printer(prefix: str, total: int, noun: str, max_lines: int = 20):
    """Return an on_progress(done) callable printing ~max_lines `[pfx] n/total` lines.

    The `[prefix] done/total` shape is machine-parseable by the server's
    _classify_log and the web UI's computeIngestProgress, which derive the
    ingestion progress bar from the log stream.
    """
    if total <= 0:
        return lambda _done: None
    # Print when `done` crosses the next of ~max_lines thresholds. Threshold-
    # crossing (not `done % step == 0`) because callers report on their own
    # cadence — e.g. compute_reachable fires every 500 pops — and a modulo
    # can be *never* satisfied (every-500 vs step 3762) leaving a whole phase
    # silent and the UI bar indeterminate.
    thresholds = [round(total * (i + 1) / (max_lines + 1)) for i in range(max_lines)]

    state = {"next": 0}

    def report(done: int):
        done = min(done, total)
        if state["next"] < len(thresholds) and done >= thresholds[state["next"]]:
            # One line per report call even when several thresholds are
            # crossed at once (callers on coarse cadences jump multiples).
            state["next"] = len([t for t in thresholds if t <= done])
            print(f"[{prefix}] {done}/{total} {noun}")

    return report


class ConstellationEngine:
    """Main engine — orchestrates parsing, detection, and graph building."""

    def __init__(self):
        pass

    def analyze(
        self,
        repo_dirs: list[tuple[str, Path]],
    ) -> ConstellationGraph:
        """
        Analyze one or more repos.

        Args:
            repo_dirs: List of (repo_name, repo_path) tuples.

        Returns:
            ConstellationGraph with all entry points, producers, and links.
        """
        repo_names = []
        repo_roots: dict[str, str] = {}

        # ── Phase 1: collect Java sources (skip tests + build output) ─
        all_files: list[tuple[str, Path, Path]] = []
        for repo_name, repo_path in repo_dirs:
            repo_names.append(repo_name)
            repo_roots[repo_name] = str(repo_path)
            print(f"[scan] {repo_name}: scanning {repo_path}")
            for jf in _walk_java_files(repo_path):
                all_files.append((repo_name, repo_path, jf))

        # ── Phase 2: build the type-aware symbol index ──────────────
        index = SymbolIndex()
        index.build(all_files, on_progress=_progress_printer("index", len(all_files), "files"))
        print(f"[index] indexed {len(index.by_fqn)} types, {len(index.methods)} methods, "
              f"{len(index.constants)} constants across {len(repo_dirs)} repo(s)")

        # ── Phase 3: detect entry points + producers (type-based) ───
        detector = EntryPointDetector(index)
        all_entry_points, all_producers = detector.scan(
            on_progress=_progress_printer("detect", len(index.by_fqn), "classes")
        )
        print(f"[detect] {len(all_entry_points)} entry points, {len(all_producers)} producers")

        # ── Phase 4: build call trees ───────────────────────────────
        print(f"\n[graph] Building call trees for {len(all_entry_points)} entry points...")
        builder = CallGraphBuilder(index)
        report = _progress_printer("graph", len(all_entry_points), "entry points")

        for i, ep in enumerate(all_entry_points, 1):
            # Find the entry method via the index and build its call tree.
            entry_methods = index.find_methods(ep.class_name, ep.method)
            entry_method = next((m for m in entry_methods if m.repo == ep.repo and m.file == ep.file), None)
            if entry_method:
                ep.call_tree = builder.build_tree(ep, entry_method)
                ep.metrics = builder.compute_metrics(ep.call_tree)
                # Genuine no-op? (body has no non-trivial calls). More accurate
                # than total_nodes, which undercounts when the enclosing class
                # can't be resolved. Consumed by find_dead_code.
                ep.metrics["thin"] = builder.is_noop_entry(entry_method.node)
            else:
                ep.call_tree = CallNode(
                    method=f"{ep.class_name}.{ep.method}",
                    file=ep.file,
                    line=ep.line,
                    class_name=ep.class_name,
                    confidence=ConfidenceTag.EXTRACTED.value,
                )
                ep.metrics = {"depth": 0, "total_nodes": 1, "unique_files": 1, "branch_count": 0, "thin": False}
            report(i)

        # ── Phase 5: cross-repo linking ─────────────────────────────
        print(f"\n[link] Finding cross-repo connections...")
        linker = CrossRepoLinker()
        links = linker.link(all_entry_points, all_producers)
        print(f"[link] Found {len(links)} cross-repo links")

        # ── Phase 6: dead-code analysis (full method reachability) ──
        # Walk the ENTIRE call graph from every entry point (no depth/node cap,
        # unlike the display trees) so deep-but-reachable methods aren't flagged.
        reached = builder.compute_reachable(
            all_entry_points,
            on_progress=_progress_printer("analyze", len(index.methods), "methods reached"),
        )
        # Candidate pool = indexed methods minus pure-contract declarations.
        # A method with no body is a contract (interface method, abstract
        # method in an abstract class, or a native method). Such methods are
        # dispatched dynamically (e.g. gRPC *ImplBase overrides, polymorphic
        # dispatch) and can never be "reached" via a static call-graph walk,
        # so flagging them unreachable is always a false positive. Keying off
        # the body (not the declaring-type kind) covers interfaces AND abstract
        # classes uniformly.
        candidate_methods = []
        for m in index.methods:
            if m.node is not None and java_ast.get_method_body(m.node) is None:
                continue
            candidate_methods.append(m)
        methods_total = len(candidate_methods)
        unreachable: list[dict] = []
        for m in candidate_methods:
            key = f"{m.class_simple}.{m.name}@{m.file}:{m.line}"
            if key in reached:
                continue
            if _is_trivial_definition(m.name):
                continue
            unreachable.append({
                "id": f"{m.repo}:{m.class_simple}.{m.name}",
                "repo": m.repo,
                "class_name": m.class_simple,
                "method": m.name,
                "file": m.file,
                "line": m.line,
            })
        print(f"[analyze] {len(unreachable)} of {methods_total} methods unreachable "
              f"(dead-code candidates)")

        # ── Phase 7: assemble graph ─────────────────────────────────
        graph = ConstellationGraph(
            repos=repo_names,
            repo_roots=repo_roots,
            entry_points=all_entry_points,
            producers=all_producers,
            cross_repo_links=links,
            methods_total=methods_total,
            unreachable_methods=unreachable,
            generated_at=datetime.now(timezone.utc).isoformat(),
            engine_version="0.2.0",
        )

        return graph


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Constellation — deterministic codebase entry point mapper"
    )
    parser.add_argument(
        "repos",
        nargs="+",
        help="Paths to repo directories to analyze",
    )
    parser.add_argument(
        "--output", "-o",
        default="graph.json",
        help="Output JSON file path (default: graph.json)",
    )
    parser.add_argument(
        "--repo-names",
        nargs="*",
        help="Custom repo names (must match number of repo paths)",
    )

    args = parser.parse_args()

    # Resolve repo paths
    repo_dirs = []
    for i, repo_path in enumerate(args.repos):
        p = Path(repo_path).resolve()
        if not p.exists():
            print(f"Error: {p} does not exist", file=sys.stderr)
            sys.exit(1)
        if args.repo_names and i < len(args.repo_names):
            name = args.repo_names[i]
        else:
            name = p.name
        repo_dirs.append((name, p))

    # Run engine
    engine = ConstellationEngine()
    graph = engine.analyze(repo_dirs)

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(graph.to_json())
    print(f"\n[done] Graph written to {output_path}")
    print(f"[done] {len(graph.entry_points)} entry points, "
          f"{len(graph.producers)} producers, "
          f"{len(graph.cross_repo_links)} cross-repo links")


if __name__ == "__main__":
    main()
