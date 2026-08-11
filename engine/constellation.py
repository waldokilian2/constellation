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
from .models import ConstellationGraph, CallNode, ConfidenceTag


def _is_test_file(path: Path) -> bool:
    """Standard Maven/Gradle test detection (src/test/, *Test/*Tests/*IT)."""
    s = str(path).replace("\\", "/")
    if "/src/test/" in s or "/test/" in s:
        return True
    stem = path.stem
    return stem.endswith("Test") or stem.endswith("Tests") or stem.endswith("IT")


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

        # ── Phase 1: collect Java sources (skip tests) ─────────────
        all_files: list[tuple[str, Path, Path]] = []
        for repo_name, repo_path in repo_dirs:
            repo_names.append(repo_name)
            repo_roots[repo_name] = str(repo_path)
            print(f"[scan] {repo_name}: scanning {repo_path}")
            java_files = sorted(p for p in repo_path.rglob("*.java") if not _is_test_file(p))
            for jf in java_files:
                all_files.append((repo_name, repo_path, jf))

        # ── Phase 2: build the type-aware symbol index ──────────────
        index = SymbolIndex()
        index.build(all_files)
        print(f"[scan] indexed {len(index.by_fqn)} types, {len(index.methods)} methods, "
              f"{len(index.constants)} constants across {len(repo_dirs)} repo(s)")

        # ── Phase 3: detect entry points + producers (type-based) ───
        detector = EntryPointDetector(index)
        all_entry_points, all_producers = detector.scan()
        print(f"[scan] {len(all_entry_points)} entry points, {len(all_producers)} producers")

        # ── Phase 4: build call trees ───────────────────────────────
        print(f"\n[graph] Building call trees for {len(all_entry_points)} entry points...")
        builder = CallGraphBuilder(index)

        for ep in all_entry_points:
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

        # ── Phase 5: cross-repo linking ─────────────────────────────
        print(f"\n[link] Finding cross-repo connections...")
        linker = CrossRepoLinker()
        links = linker.link(all_entry_points, all_producers)
        print(f"[link] Found {len(links)} cross-repo links")

        # ── Phase 6: dead-code analysis (full method reachability) ──
        # Walk the ENTIRE call graph from every entry point (no depth/node cap,
        # unlike the display trees) so deep-but-reachable methods aren't flagged.
        reached = builder.compute_reachable(all_entry_points)
        # Candidate pool = indexed methods minus pure-contract declarations.
        # Interface methods have no body — they're contracts, not dead code, so
        # they can never be "unreachable" in a meaningful sense.
        candidate_methods = []
        for m in index.methods:
            ci = index.class_for_method(m)
            if ci and ci.kind == "interface":
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
        print(f"[scan] {len(unreachable)} of {methods_total} methods unreachable "
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
