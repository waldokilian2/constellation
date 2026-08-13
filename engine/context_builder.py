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

    def __init__(self, graph: dict, boards: list | None = None):
        self.graph = graph
        self.boards = boards or []  # connected boards (per project) for chat context

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

        # ── Section 9: Connected boards ─────────────────────────
        boards = self._boards_section()
        if boards:
            sections.append(boards)

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

        # ── Connected boards ─────────────────────────────────────
        boards = self._boards_section()
        if boards:
            sections.append(boards)

        # ── Hint to use tools ────────────────────────────────────
        sections.append(
            "You have tools available to explore the codebase in detail: "
            "search_code, find_callers, trace_path, get_channel_flow, "
            "list_channels, get_node, get_source, get_architecture_overview, "
            "find_orphans, find_cycles, find_dead_code. "
            "Use them to answer specific questions accurately. The chat "
            "renders markdown live: use ```mermaid fenced blocks for flow "
            "diagrams and ```html fenced blocks for custom visuals."
        )

        return "\n\n".join(sections)

    def build_boards_prompt(self) -> str:
        """Board-focused system prompt for the Boards view chat.

        When the user is on the Boards view, the assistant's job is the boards —
        not the whole system. It gets the connected boards, their columns, and
        their items, and is steered to answer board questions (triage, status
        moves, summaries). Architecture context is still available to answer
        'which service does this item touch?' when a board item links to code.
        """
        sections = [
            "You are a project/task assistant for Constellation's Boards view. "
            "The user is working with GitHub Project / Issues boards synced via "
            "the GitHub MCP server. Help them understand, triage, and manage the "
            "boards: summarize what is in each column, answer questions about "
            "specific items, suggest status moves, and explain what work is "
            "tracked. The data below is the current synced state — trust it as "
            "fact.\n\n"
            "Keep your answers focused on the boards and their items. You may "
            "reference the architecture when relevant (e.g. which service a "
            "linked item touches), but unless the user asks about code, the "
            "boards are the subject."
        ]
        boards = self._boards_section()
        sections.append(boards if boards else "No boards are connected in this project yet — suggest connecting one.")
        sections.append(
            "You have board tools you can call to inspect and change the board: "
            "``list_boards``, ``list_board_items``, ``move_board_item`` (move a "
            "card to a different status column — this writes to GitHub), and "
            "``add_board_comment`` (comment on an item's issue). Use them to act "
            "on the board when the user asks (e.g. move an item, add a comment), "
            "not just to describe it. After a move/comment, confirm what you did."
        )
        return "\n\n".join(sections)

    def build_planner_prompt(self, repo: str = "") -> str:
        """
        Build a system prompt for AI change planning mode.

        Gives the AI a full architecture overview and instructs it to
        gather context, reason about impact, and present the plan — tables
        in the chat, flows/diagrams in the plan-preview panel via the
        ``render_diagram`` tool (never as embedded Mermaid or HTML blocks).
        """
        sections = []

        # ── Role ─────────────────────────────────────────────────
        sections.append(
            "## PLANNER MODE\n\n"
            "You are an AI change planner for a microservice codebase mapped by "
            "Constellation. The user will describe a change they want to make. "
            "Your job:\n\n"
            "1. **Gather context** — use tools (search_code, "
            "get_architecture_overview, get_channel_flow, list_channels, "
            "get_node, trace_path) to understand the current architecture "
            "and how the affected repos, entry points, and data flows work.\n\n"
            "2. **Reason about impact** — what repos, entry points, producers, "
            "and data flows need to change? What are the knock-on effects?\n\n"
            "3. **Present the plan** — start with a table for the system-wide "
            "view (Channel | Broker | Producer | Consumer | Status), then "
            "use the ``render_diagram`` tool to add one Mermaid flow per "
            "new/changed path to the plan-preview panel. Use exact code "
            "references inline (Class.method, file:line) and health "
            "annotations (⚠️ was ORPHAN — now REVIVED). Never embed diagrams "
            "or Mermaid/HTML blocks in your chat text — the panel is where "
            "visuals live.\n\n"
            "4. **Explain the plan** — give a structured text breakdown in the "
            "chat: what changes in each repo, what new code is needed, what "
            "existing entry points/producers are affected, and any risks or "
            "dependencies.\n\n"
            "Be thorough but concise. When uncertain about "
            "implementation details, note assumptions."
        )

        # ── Chat-text output rules (NO diagrams here) ─────────────
        sections.append(
            "## CHAT-TEXT OUTPUT RULES\n\n"
            "Your chat-text answer is plain markdown. It must NOT contain "
            "diagrams, Mermaid blocks, or HTML — visuals go through the "
            "``render_diagram`` tool into the dedicated plan-preview panel. "
            "The chat is for:\n\n"
            "- **Tables** for the system-wide view (Channel | Broker | "
            "Producer | Consumer | Status).\n"
            "- **Text breakdowns** of what changes per repo.\n"
            "- **Code references**: Class.method, repo-relative file:line.\n"
            "- **Health annotations** inline (⚠️ ORPHAN, ⚰️ DEAD, 🟢 REVIVED).\n\n"
            "Do NOT use ```mermaid or ```html fenced blocks in your reply — "
            "any diagram you want to show must be added via the "
            "``render_diagram`` tool with action ``add``."
        )

        # ── Preview-panel diagrams (the render_diagram tool) ─────
        sections.append(
            "## PLAN-PREVIEW PANEL (render_diagram tool)\n\n"
            "The right-side preview panel is a separate canvas you drive "
            "**explicitly** with the ``render_diagram`` tool. It is the "
            "canonical place for the plan's visuals. Each diagram you add "
            "shows there with a header (title) and a Mermaid or HTML body, "
            "and persists with the conversation.\n\n"
            "How to use it:\n"
            "- To **show** a visual, call ``render_diagram`` with action "
            "``add``, a short ``header``, the diagram ``code``, and "
            "``kind`` (``mermaid`` by default; ``html`` only for visuals "
            "Mermaid cannot express — HTML/CSS only, no scripts).\n"
            "- To **update** an existing diagram, call it with action "
            "``replace`` and the diagram ``diagram_id`` (call ``get`` first "
            "if you don't know the id). To **remove** one, action "
            "``remove`` with the id. Action ``clear`` wipes the panel.\n"
            "- **STOP after the user's visuals are shown.** Create the "
            "diagram(s) the request needs ONCE, then call ``task_complete`` "
            "with status 'complete'. Do NOT keep adding variations of the "
            "same diagram — if you want to improve one you already added, "
            "use ``replace`` with its ``diagram_id``, never a second "
            "``add``. Repeated ``add`` calls for the same visual are a bug.\n"
            "- **Mermaid is validated before it is shown.** If your "
            "diagram has a syntax error the tool returns ``ok: false`` "
            "with the parse error and does NOT add it. On the FIRST "
            "rejection, fix the exact error and retry once. If it fails "
            "again, simplify the diagram (drop fancy labels/shapes, use "
            "plain ``A --> B`` edges) instead of retrying the same "
            "pattern. Bare edge labels are auto-quoted for you, but write "
            "``|\"label\"|`` yourself anyway. A node id may not be a quoted "
            "path with a trailing bracket — use ``myId[\"GET /api/orders/{id}\"]`` "
            "(simple id, label in the brackets) not ``\"/api/orders/{id}\"[http]``.\n"
            "- Mermaid syntax rules above apply here too: keep it simple "
            "and always double-quote edge labels.\n\n"
            "**Diagrams live in the panel, NOT in chat text.** Your chat "
            "reply must never contain ```mermaid or ```html fenced blocks "
            "— instead call the ``render_diagram`` tool. Keep the chat for "
            "the written breakdown (tables, file references, per-repo "
            "changes, risks) and put every flow/diagram in the panel."
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
        arch_lines.append(f"  {len(links)} cross-repo channels")

        # Type breakdown
        type_counts: dict[str, int] = {}
        for ep in entry_points:
            t = ep.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        if type_counts:
            type_str = ", ".join(f"{count} {t}" for t, count in sorted(type_counts.items()))
            arch_lines.append(f"  Entry point types: {type_str}")

        if links:
            arch_lines.append("  Channels:")
            for link in links:
                prod_repos = self._repos_from_ids(link.get("producers", []))
                cons_repos = self._repos_from_ids(link.get("consumers", []))
                kind = link.get("kind", "message")
                verb = link.get("verb", "")
                kind_tag = f" [{kind}" + (f" {verb}" if verb else "") + "]"
                arch_lines.append(
                    f'    "{link["channel"]}"{kind_tag}: '
                    f"{prod_repos} → {cons_repos}"
                )

        sections.append("\n".join(arch_lines))

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

        # ── Repo focus note ──────────────────────────────────────
        if repo:
            sections.append(
                f"The user arrived from the **{repo}** service view. "
                f"Start by scoping changes from that service outward."
            )

        # ── Connected boards ─────────────────────────────────────
        boards = self._boards_section()
        if boards:
            sections.append(boards)

        # ── Tool hint ────────────────────────────────────────────
        sections.append(
            "You have tools available to explore the codebase in detail: "
            "search_code, find_callers, trace_path, get_channel_flow, "
            "list_channels, get_node, get_source, get_architecture_overview, "
            "find_orphans, find_cycles, find_dead_code. "
            "Use them to understand the current state before planning — "
            "especially find_orphans and find_dead_code, which surface the "
            "half-wired channels and unreachable code your plan can revive.\n"
            "Use the ``render_diagram`` tool for every visual (one Mermaid "
            "flow per new/changed path, one HTML preview per custom visual). "
            "Follow the CHAT-TEXT OUTPUT RULES above for your text reply "
            "(tables, code references, health annotations only)."
        )

        return "\n\n".join(sections)

    # ── Private helpers ──────────────────────────────────────────

    def _boards_section(self) -> str:
        """A CONNECTED BOARDS block for the AI, or '' if none are connected.

        Gives the assistant visibility into the project's synced external boards
        (e.g. a GitHub Project) so it can answer questions like 'how many items
        are in Backlog?' or 'what does issue #49 reference?'.
        """
        if not self.boards:
            return ""
        lines = ["CONNECTED BOARDS (synced via the GitHub MCP server):"]
        for b in self.boards:
            items = b.get("items") or []
            lines.append(
                f"  {b.get('name') or b.get('id', '?')} "
                f"[{b.get('provider', '?')} / {b.get('kind', '?')}] — {len(items)} items"
            )
            statuses: dict[str, int] = {}
            for it in items:
                s = it.get("status") or "no status"
                statuses[s] = statuses.get(s, 0) + 1
            if statuses:
                cols = ", ".join(f"{s}: {n}" for s, n in sorted(statuses.items()))
                lines.append(f"    columns: {cols}")
            if items:
                lines.append("    items:")
                for it in items[:10]:
                    lines.append(
                        f"      #{it.get('number', '')} [{it.get('status') or '?'}] "
                        f"{it.get('title', '')[:90]}"
                    )
        return "\n".join(lines)

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
