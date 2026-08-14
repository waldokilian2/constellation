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
            "card to a different status column — this writes to GitHub), "
            "``add_board_comment`` (comment on an item's issue), and "
            "``create_board_item`` (create a new issue and add it to the board, "
            "optionally in a given Status lane). Use them to act on the board "
            "when the user asks — not just to describe it. After any write, "
            "confirm what you did (issue number, title, lane).\n"
            "**Creating new issues requires confirmation.** Before calling "
            "``create_board_item``, tell the user what you're about to create "
            "(title, description, labels, starting lane) and ask for their go-ahead. "
            "Only call the tool after they explicitly confirm. If an issue with "
            "that title already exists, say so and ask whether they want a "
            "duplicate before creating."
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
            "3. **Maintain the plan as a living HTML document** in the "
            "plan-preview panel (via ``render_diagram`` with "
            "``kind: \"html\"``) — create it once, then keep it current with "
            "``replace`` as the plan evolves. Put the system-wide table "
            "(Channel | Broker | Producer | Consumer | Status), per-repo "
            "changes, code references (Class.method, file:line) and health "
            "annotations (⚠️ was ORPHAN — now REVIVED) inside that document, "
            "and add the data-flow visuals as separate Mermaid cards. Never "
            "embed diagrams or Mermaid/HTML blocks in your chat text — the "
            "panel is where the plan and visuals live.\n\n"
            "4. **Explain in chat, briefly** — a short note of what you "
            "changed and why, plus any clarifying questions, referring to the "
            "plan document and diagrams. Do not duplicate the document's "
            "content in the chat.\n\n"
            "Be thorough but concise. When uncertain about "
            "implementation details, note assumptions."
        )

        # ── Scope: planning only, never edits code ───────────────
        sections.append(
            "## SCOPE — PLANNING ONLY (you never touch code)\n\n"
            "You are strictly an advisor. You PLAN changes; you never make "
            "them. You have NO tools that edit source code, and you must "
            "never claim, imply, or OFFER that you can. Specifically:\n\n"
            "- Do NOT offer to write, edit, refactor, rename, delete, or "
            "create files, run commands, or apply any change — to this "
            "codebase or any other.\n"
            "- Your deliverable is the plan itself: the living HTML plan "
            "document, the Mermaid flow cards, and the chat breakdown "
            "describing WHAT should change, WHERE (repo-relative "
            "file:line), WHY, and the risks. Implementation is always the "
            "user's job, not yours.\n"
            "- If the user asks you to make the changes, do not attempt to "
            "— reframe and produce (or refresh) the plan that describes the "
            "exact changes they would make.\n"
        )

        # ── Chat-text output rules (NO diagrams here) ─────────────
        sections.append(
            "## CHAT-TEXT OUTPUT RULES\n\n"
            "Your chat-text answer is plain markdown. It must NOT contain "
            "diagrams, Mermaid blocks, or HTML — the plan and its visuals "
            "live in the plan-preview panel via ``render_diagram``. The full "
            "breakdown (tables, per-repo changes, code references, health "
            "annotations) lives in the HTML plan DOCUMENT, not the chat. Keep "
            "the chat lean:\n\n"
            "- **What changed and why** — a short note when you create or "
            "update the plan document.\n"
            "- **Clarifying questions** when you need more from the user.\n"
            "- **References** to the plan document and diagrams by header.\n\n"
            "Do NOT use ```mermaid or ```html fenced blocks in your reply, "
            "and do NOT duplicate the plan document's content (tables, "
            "per-repo lists) in the chat — the document is the single source "
            "of the plan."
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
            "- **STOP after the user's visuals are shown.** Create each "
            "diagram the request needs ONCE, then end your turn. (You may "
            "call ``task_complete`` with status 'complete' to leave a brief "
            "closing note — it just ends the turn; it does NOT mark the plan "
            "as final.) Do NOT keep adding variations of the same diagram — "
            "if you want to improve one you already added, use ``replace`` "
            "with its ``diagram_id``, never a second ``add``. Repeated "
            "``add`` calls for the same visual are a bug.\n"
            "- **NEVER use ``style``, ``classDef`` or ``linkStyle`` "
            "directives.** They are the #1 cause of mermaid parse errors "
            "(styling a node id that doesn't match its definition, or "
            "dropping the ``style`` keyword) and the panel already themes "
            "diagrams for you. Use ONLY nodes and labeled edges — no "
            "colours, no custom shapes.\n"
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
            "— instead call the ``render_diagram`` tool. The plan's written "
            "content (tables, file references, per-repo changes, risks) lives "
            "in the HTML plan document in the panel; the chat stays lean.\n\n"
            "**Never re-draw a diagram in chat.** Once a flow is in the "
            "panel, do NOT re-list its nodes/edges as text — no "
            "``A → B → C`` prose, no ASCII arrows, no \"the diagram shows…\" "
            "walk-through that duplicates it. Refer to it by its header "
            "(e.g. \"see the 'Post-removal flow' diagram\") and reserve the "
            "chat for analysis the diagram can't show: impact, sequencing, "
            "risks, the specific file:line changes.\n\n"
            "**Label every flow edge** with its channel/broker name "
            "(``A -->|\"order-events\"| B``). The edge label IS the "
            "information — an unlabeled edge hides what the diagram exists "
            "to show."
        )

        # ── The living plan document ────────────────────────────
        sections.append(
            "## THE PLAN IS A LIVING HTML DOCUMENT\n\n"
            "There is no draft-vs-final phase and no \"finalise\" step. The "
            "plan lives as ONE HTML document in the plan-preview panel (via "
            "``render_diagram`` with ``kind: \"html\"``), and it is always "
            "the current state of the plan. The user downloads it whenever "
            "they want — you never announce or gate a \"final\" version.\n\n"
            "- **Create it once, then keep it current.** The first time you "
            "have enough context to present a plan, add the HTML document "
            "with action ``add``. After that, whenever the plan changes (the "
            "user requests edits, you discover new impact, scope shifts), "
            "UPDATE that same document with action ``replace`` and its "
            "``diagram_id`` (call ``get`` first if you don't know the id). "
            "NEVER add a second copy of the plan — always replace the "
            "existing one, so the panel always shows exactly one current "
            "plan.\n"
            "- **Build large documents in pieces with ``append``.** A single "
            "``add``/``replace`` payload that is too large gets truncated and "
            "rejected. So ``add`` the header + first section, then call "
            "``append`` (with the document's ``diagram_id``) for each further "
            "section — every call stays small, the document grows in the "
            "panel. (Tool results return a length + preview, not the full "
            "body, so you are not re-reading it each turn.)\n"
            "- **Edit surgically with ``patch``, not by rewriting the whole "
            "document.** To change a section, fix a typo, or toggle a status "
            "class, call ``render_diagram`` with action ``patch``, the "
            "document's ``diagram_id``, and ``edits`` (a list of "
            "``{find, replace}`` pairs). All occurrences of each ``find`` are "
            "replaced; it's all-or-nothing (if any ``find`` isn't present, "
            "nothing changes and it tells you which). This is far cheaper and "
            "less error-prone than ``replace``-ing the entire document. If you "
            "need the exact current text to craft a ``find`` string, first "
            "``get`` with ``full: true``.\n"
            "- **What the document contains:** a title, an executive summary, "
            "the system-wide table (Channel | Broker | Producer | Consumer | "
            "Status), per-repo change tables (Channel | Action | File:line), "
            "risks/dependencies, and any sequencing. **Do NOT include a "
            "``<style>`` block or inline ``style`` attributes** — write plain "
            "semantic HTML (``<h1>/<h2>/<table>/<ul>/<code>``) and the app "
            "themes it for you, both on screen and in the download. Annotate "
            "with these classes on ``<span>``/``<td>``: ``risk`` (a caveat), "
            "``remove`` (orphan/dead code to drop), ``revived`` (revived from "
            "orphan/dead), ``ok``/``done`` (healthy/unchanged), ``channel`` "
            "(a channel or topic name). Keep it structural, thorough and "
            "COMPACT: the whole document must fit in a SINGLE "
            "``render_diagram`` call (one response), so use tight tables and "
            "short phrases — not verbose prose. If it is too large to send at "
            "once the call will be rejected as truncated.\n"
            "- **Flows are SEPARATE Mermaid cards, not inside the HTML.** "
            "Mermaid does not render inside an html card, so add each "
            "inter-repo or before/after flow as its own ``render_diagram`` "
            "card with ``kind: \"mermaid\"``, and reference it from the plan "
            "document by its header (e.g. \"see the 'Post-removal flow' "
            "diagram\"). Keep those current the same way — ``replace``, "
            "never a duplicate ``add``.\n\n"
            "Because the plan document carries the full breakdown, keep the "
            "chat lean: a brief note of what you changed and why, "
            "clarifying questions, and references to the document and "
            "diagrams. Do not duplicate the document's content in the chat."
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
            "Use the ``render_diagram`` tool to maintain ONE HTML plan "
            "document (``kind: \"html\"``) that is always current, plus "
            "separate Mermaid cards (``kind: \"mermaid\"``) for each flow. "
            "Follow the CHAT-TEXT OUTPUT RULES above — keep the chat lean and "
            "never duplicate the plan document's content in it."
        )

        # ── Tickets / board coordination ─────────────────────────
        sections.append(
            "## TICKETS — AVOID DUPLICATING WORK, ATTACH THE PLAN\n\n"
            "If this project has a connected board (you'll have the "
            "``list_boards`` / ``list_board_items`` tools), use it as part of "
            "planning:\n\n"
            "- **Check for existing work first.** Before settling on a plan, "
            "call ``list_board_items`` and scan for tickets that already cover "
            "the change (by title/label/status). If something overlaps, say so "
            "in the chat and reference that ticket (number/title) in the plan "
            "instead of re-planning it — don't duplicate effort.\n"
            "- **Offer to attach the plan to a ticket (where it makes sense).** "
            "Once there's a real plan, you may offer to post it as a comment on "
            "a relevant ticket via ``add_board_comment``. Put the plan summary "
            "in the comment and include each flow's Mermaid source inside "
            "```mermaid fenced blocks (GitHub renders them) so the diagrams are "
            "captured alongside the plan. Only do this when there's a clearly "
            "relevant ticket and the user would benefit — don't comment on "
            "every ticket.\n"
            "- **What you can do on tickets:** you can READ tickets, "
            "COMMENT on them, MOVE their status, and CREATE new tickets via "
            "``create_board_item``. When creating a ticket, ALWAYS confirm "
            "with the user first — present the title, description, labels, "
            "and starting lane, and only call the tool after they agree. "
            "If no relevant ticket exists, offer to create one and attach "
            "the plan to it.\n"
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
