"""
Context Builder — assembles structured graph data into a system prompt.

Instead of dumping raw source code to the AI, this builds a rich context
that includes the call tree, cross-repo connections, and relationships
that the deterministic engine already extracted. The AI gets architectural
understanding for free — it doesn't have to re-derive relationships from
reading source code.
"""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class NodeContext:
    """Everything needed to build an AI context for a specific node."""
    graph: dict
    entry_point: dict
    node: dict  # the specific call_tree node selected
    source_content: str = ""
    source_line_count: int = 0


class ContextBuilder:
    """Builds structured system prompts from graph data."""

    def __init__(self, graph: dict):
        self.graph = graph

    def build_system_prompt(
        self,
        entry_point: dict,
        node: dict,
        source_content: str = "",
        source_line_count: int = 0,
    ) -> str:
        """
        Build a comprehensive system prompt for the AI.

        This gives the AI:
        - The overall architecture (repos, channels)
        - The specific entry point and its type
        - The full call tree (what executes)
        - Cross-repo connections (where messages flow)
        - The selected node's relationships (called by, calls)
        - Source code
        """
        sections = []

        # ── Section 1: Role ─────────────────────────────────────
        sections.append(
            "You are a code analysis assistant for Constellation, a tool that "
            "deterministically maps Java Spring Boot microservice architectures "
            "using AST parsing. You are given structured data that was extracted "
            "from the codebase — trust it as fact. Answer questions concisely "
            "and accurately. When you reference other functions or services, "
            "use the names and relationships provided in the context."
        )

        # ── Section 2: Architecture overview ────────────────────
        repos = self.graph.get("repos", [])
        links = self.graph.get("cross_repo_links", [])
        if repos:
            arch_lines = [f"ARCHITECTURE OVERVIEW:"]
            arch_lines.append(f"  {len(repos)} repositories: {', '.join(repos)}")
            if links:
                arch_lines.append(f"  Message channels connecting repos:")
                for link in links:
                    prod_repos = self._repos_from_ids(link.get("producers", []))
                    cons_repos = self._repos_from_ids(link.get("consumers", []))
                    arch_lines.append(
                        f"    \"{link['channel']}\": {prod_repos} → {cons_repos}"
                    )
            sections.append("\n".join(arch_lines))

        # ── Section 3: Entry point details ──────────────────────
        ep_lines = ["ENTRY POINT BEING ANALYZED:"]
        ep_lines.append(f"  ID: {entry_point.get('id', 'unknown')}")
        ep_lines.append(f"  Repo: {entry_point.get('repo', 'unknown')}")
        ep_lines.append(f"  Type: {entry_point.get('type', 'unknown')}")
        ep_lines.append(f"  Channel: {entry_point.get('channel', 'unknown')}")
        if entry_point.get("message_type"):
            ep_lines.append(f"  Message type: {entry_point['message_type']}")
        if entry_point.get("metrics"):
            m = entry_point["metrics"]
            ep_lines.append(
                f"  Complexity: {m.get('total_nodes', 0)} calls, "
                f"depth {m.get('depth', 0)}, "
                f"{m.get('unique_files', 0)} files"
            )
        sections.append("\n".join(ep_lines))

        # ── Section 4: Call tree ────────────────────────────────
        call_tree = entry_point.get("call_tree")
        if call_tree:
            tree_lines = ["EXECUTION PATH (what runs when this entry point is triggered):"]
            tree_lines.append(self._format_call_tree(call_tree, indent="  "))
            sections.append("\n".join(tree_lines))

        # ── Section 5: Cross-repo connections for this node ─────
        connections = self._find_connections_for_node(node, entry_point)
        if connections:
            conn_lines = ["CROSS-REPO CONNECTIONS (messages that leave or enter this service):"]
            for c in connections:
                conn_lines.append(f"  {c}")
            sections.append("\n".join(conn_lines))

        # ── Section 6: Node relationships ───────────────────────
        rels = self._find_relationships(entry_point, node)
        rel_lines = ["CURRENT NODE RELATIONSHIPS:"]
        rel_lines.append(f"  Selected node: {self._node_display_name(node)}")
        if rels["parent"]:
            rel_lines.append(f"  Called by: {self._node_display_name(rels['parent'])}")
        if rels["calls"]:
            calls_str = ", ".join(self._node_display_name(c) for c in rels["calls"])
            rel_lines.append(f"  Calls: {calls_str}")
        if rels["siblings"]:
            sib_str = ", ".join(self._node_display_name(s) for s in rels["siblings"])
            rel_lines.append(f"  Siblings (same level): {sib_str}")
        sections.append("\n".join(rel_lines))

        # ── Section 7: Source code ──────────────────────────────
        if source_content:
            src_lines = [f"SOURCE CODE ({source_line_count} lines):"]
            src_lines.append(f"```java")
            # If the file is very long, note it's truncated
            if source_line_count > 200:
                src_lines.append(f"// (file has {source_line_count} lines, showing full file)")
            src_lines.append(source_content)
            src_lines.append("```")
            sections.append("\n".join(src_lines))

        # ── Section 8: Current node focus ───────────────────────
        focus_lines = ["CURRENT FOCUS:"]
        focus_lines.append(f"  The user has selected: {self._node_display_name(node)}")
        if node.get("file") and node.get("line"):
            focus_lines.append(f"  Location: {Path(node['file']).name}:{node['line']}")
        focus_lines.append(
            "  When answering, focus on this specific function and its role "
            "in the execution path above."
        )
        sections.append("\n".join(focus_lines))

        return "\n\n".join(sections)

    def build_global_prompt(self) -> str:
        """
        Build a system prompt for global/cross-repo questions.
        Used when the user is on the galaxy view and asks architecture-level questions.
        No specific entry point or node is selected.
        """
        sections = []

        # ── Role ─────────────────────────────────────────────────
        sections.append(
            "You are a code analysis assistant for Constellation, a tool that "
            "deterministically maps Java Spring Boot microservice architectures "
            "using AST parsing. You are given structured data that was extracted "
            "from the codebase — trust it as fact. "
            "The user is asking a question about the overall architecture, not "
            "a specific function. Answer concisely and use the available tools "
            "to explore the codebase when needed. When referencing services, "
            "channels, or entry points, use the exact names from the data."
        )

        # ── Architecture overview ────────────────────────────────
        repos = self.graph.get("repos", [])
        links = self.graph.get("cross_repo_links", [])
        entry_points = self.graph.get("entry_points", [])
        producers = self.graph.get("producers", [])

        arch_lines = ["ARCHITECTURE OVERVIEW:"]
        arch_lines.append(f"  {len(repos)} repositories: {', '.join(repos)}")
        arch_lines.append(f"  {len(entry_points)} entry points")
        arch_lines.append(f"  {len(producers)} message producers")
        arch_lines.append(f"  {len(links)} cross-repo message channels")

        # Type breakdown
        type_counts: dict[str, int] = {}
        for ep in entry_points:
            t = ep.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        if type_counts:
            type_str = ", ".join(f"{count} {t}" for t, count in sorted(type_counts.items()))
            arch_lines.append(f"  Entry point types: {type_str}")

        if links:
            arch_lines.append("  Message channels:")
            for link in links:
                prod_repos = self._repos_from_ids(link.get("producers", []))
                cons_repos = self._repos_from_ids(link.get("consumers", []))
                arch_lines.append(
                    f'    "{link["channel"]}": {prod_repos} → {cons_repos}'
                )

        sections.append("\n".join(arch_lines))

        # ── All entry points summary ─────────────────────────────
        if entry_points:
            ep_lines = ["ENTRY POINTS:"]
            for ep in entry_points:
                method = self._node_display_name(ep)
                ep_lines.append(
                    f"  {ep['repo']} → {method} "
                    f"[{ep.get('type', '?')}: {ep.get('channel', '?')}]"
                )
            sections.append("\n".join(ep_lines))

        # ── Per-repo summary ─────────────────────────────────────
        repo_summary: dict[str, dict] = {}
        for ep in entry_points:
            r = ep["repo"]
            if r not in repo_summary:
                repo_summary[r] = {"entry_points": 0, "producers": 0, "types": set()}
            repo_summary[r]["entry_points"] += 1
            repo_summary[r]["types"].add(ep.get("type", ""))
        for prod in producers:
            r = prod["repo"]
            if r not in repo_summary:
                repo_summary[r] = {"entry_points": 0, "producers": 0, "types": set()}
            repo_summary[r]["producers"] += 1

        repo_lines = ["REPO SUMMARY:"]
        for r, data in sorted(repo_summary.items()):
            types_str = ", ".join(sorted(t for t in data["types"] if t))
            repo_lines.append(
                f"  {r}: {data['entry_points']} entry points "
                f"({types_str}), {data['producers']} producers"
            )
        sections.append("\n".join(repo_lines))

        # ── Hint to use tools ────────────────────────────────────
        sections.append(
            "You have tools available to explore the codebase in detail: "
            "search_code, find_callers, trace_path, get_channel_flow, "
            "list_channels, get_node, get_source, get_architecture_overview. "
            "Use them to answer specific questions accurately."
        )

        return "\n\n".join(sections)

    # ── Private helpers ──────────────────────────────────────────

    def _format_call_tree(self, node: dict, indent: str = "", depth: int = 0) -> str:
        """Recursively format a call tree as indented text."""
        if not node:
            return ""
        prefix = indent + ("  " * depth)
        name = self._node_display_name(node)
        confidence = node.get("confidence", "")
        marker = ""

        # Check if this node is a producer
        is_producer = self._is_producer(node)
        if is_producer:
            marker = " [⚡ PRODUCER]"

        line = f"{prefix}→ {name}{marker}"
        if confidence and confidence not in ("EXTRACTED",):
            line += f" [{confidence}]"

        children = node.get("children", [])
        for child in children:
            line += "\n" + self._format_call_tree(child, indent, depth + 1)

        return line

    def _node_display_name(self, node: dict) -> str:
        """Get a clean display name for a node."""
        method = node.get("method", "")
        class_name = node.get("class_name", "")
        if class_name and method and "." not in method:
            return f"{class_name}.{method}"
        return method or class_name or node.get("id", "unknown")

    def _is_producer(self, node: dict) -> bool:
        """Check if this node is a known producer (sends messages)."""
        producers = self.graph.get("producers", [])
        node_name = self._node_display_name(node)
        for p in producers:
            if p.get("method") == node_name:
                return True
        # Also check by method invocation patterns
        method = node.get("method", "")
        if any(pattern in method for pattern in (
            "convertAndSend", "send", "publishEvent"
        )):
            return True
        return False

    def _find_connections_for_node(
        self, node: dict, entry_point: dict
    ) -> list[str]:
        """Find cross-repo connections relevant to this node."""
        connections = []
        links = self.graph.get("cross_repo_links", [])

        # Check if the node's entry point produces to a channel
        ep_repos = entry_point.get("repo", "")
        seen_channels = set()
        for p in self.graph.get("producers", []):
            if p.get("repo") == ep_repos:
                channel = p.get("channel", "")
                if channel in seen_channels:
                    continue
                seen_channels.add(channel)
                # Find consumers of this channel
                for link in links:
                    if link.get("channel") == channel:
                        cons_repos = self._repos_from_ids(link.get("consumers", []))
                        if cons_repos:
                            connections.append(
                                f'Produces to "{channel}" → consumed by: {", ".join(cons_repos)}'
                            )

        # Check if this entry point itself consumes from a channel
        ep_channel = entry_point.get("channel", "")
        ep_type = entry_point.get("type", "")
        if ep_type in ("kafka-consumer", "rabbitmq-consumer", "jms-consumer", "sqs-consumer", "event-listener"):
            for link in links:
                if link.get("channel") == ep_channel:
                    prod_repos = self._repos_from_ids(link.get("producers", []))
                    if prod_repos:
                        connections.append(
                            f'Consumes from "{ep_channel}" ← produced by: {", ".join(prod_repos)}'
                        )

        return connections

    def _find_relationships(self, entry_point: dict, target: dict) -> dict:
        """Find the parent, calls, and siblings of a node in the call tree."""
        tree = entry_point.get("call_tree")
        if not tree:
            return {"parent": None, "calls": [], "siblings": []}

        result = {"parent": None, "calls": [], "siblings": []}

        def walk(node, parent, siblings):
            if self._same_node(node, target):
                result["parent"] = parent
                result["calls"] = node.get("children", [])
                result["siblings"] = siblings
                return True
            children = node.get("children", [])
            for i, child in enumerate(children):
                other_children = [c for j, c in enumerate(children) if j != i]
                if walk(child, node, other_children):
                    return True
            return False

        walk(tree, None, [])
        return result

    @staticmethod
    def _same_node(a: dict, b: dict) -> bool:
        """Check if two nodes are the same."""
        return (
            a.get("method") == b.get("method")
            and a.get("file") == b.get("file")
            and a.get("line") == b.get("line")
        )

    def _repos_from_ids(self, ids: list[str]) -> list[str]:
        """Extract unique repo names from entry point / producer IDs."""
        repos = set()
        for id_str in ids:
            if ":" in id_str:
                repos.add(id_str.split(":")[0])
        return sorted(repos) if repos else ["unknown"]
