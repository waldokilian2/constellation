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

from .parser import JavaParser
from .entry_detector import EntryPointDetector
from .call_graph import CallGraphBuilder
from .cross_repo import CrossRepoLinker
from .models import ConstellationGraph


class ConstellationEngine:
    """Main engine — orchestrates parsing, detection, and graph building."""

    def __init__(self):
        self.java_parser = JavaParser()

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
        all_entry_points = []
        all_producers = []
        all_methods = []
        repo_names = []
        repo_roots: dict[str, str] = {}

        # ── Phase 1: Detect entry points and producers ────────────
        for repo_name, repo_path in repo_dirs:
            repo_names.append(repo_name)
            repo_roots[repo_name] = str(repo_path)
            print(f"[scan] {repo_name}: scanning {repo_path}")

            detector = EntryPointDetector(repo_name)
            entries, producers, methods = detector.scan_directory(repo_path)

            all_entry_points.extend(entries)
            all_producers.extend(producers)
            all_methods.extend(methods)
            print(f"[scan] {repo_name}: {len(entries)} entry points, "
                  f"{len(producers)} producers, {len(methods)} methods indexed")

        # ── Phase 2: Build call trees ──────────────────────────────
        print(f"\n[graph] Building call trees for {len(all_entry_points)} entry points...")
        builder = CallGraphBuilder(all_methods)

        # Build a lookup: "ClassName.methodName" → ClassMethod.node
        method_node_lookup = {}
        for m in all_methods:
            key = f"{m.class_name}.{m.name}"
            if key not in method_node_lookup:
                method_node_lookup[key] = m

        for ep in all_entry_points:
            method_key = f"{ep.class_name}.{ep.method}"
            method_cm = method_node_lookup.get(method_key)
            if method_cm and method_cm.node:
                ep.call_tree = builder.build_tree(ep, method_cm.node)
                ep.metrics = builder.compute_metrics(ep.call_tree)
            else:
                # Method node not found — create a minimal tree
                from .models import CallNode
                ep.call_tree = CallNode(
                    method=f"{ep.class_name}.{ep.method}",
                    file=ep.file,
                    line=ep.line,
                    class_name=ep.class_name,
                    confidence="EXTRACTED",
                )
                ep.metrics = {"depth": 0, "total_nodes": 1, "unique_files": 1, "branch_count": 0}

        # ── Phase 3: Cross-repo linking ────────────────────────────
        print(f"\n[link] Finding cross-repo connections...")
        linker = CrossRepoLinker()
        links = linker.link(all_entry_points, all_producers)
        print(f"[link] Found {len(links)} cross-repo links")

        # ── Phase 4: Assemble graph ────────────────────────────────
        graph = ConstellationGraph(
            repos=repo_names,
            repo_roots=repo_roots,
            entry_points=all_entry_points,
            producers=all_producers,
            cross_repo_links=links,
            generated_at=datetime.now(timezone.utc).isoformat(),
            engine_version="0.1.0",
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
