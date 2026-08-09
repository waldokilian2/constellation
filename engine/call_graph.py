"""
Call graph builder — traces execution paths from entry points.

For each entry point, walks the method body, finds all method invocations, and
resolves them to their definitions using the type-aware :class:`JavaIndex`
(field type + interface→impl + import-aware). Recurses up to MAX_DEPTH levels
deep, tagging each edge with a confidence level.
"""
from __future__ import annotations
from typing import Optional
from tree_sitter import Node

from .parser import JavaParser
from .java_index import JavaIndex, ClassInfo
from .models import EntryPoint, CallNode


MAX_DEPTH = 4
MAX_NODES = 50  # Safety valve — don't build infinite trees


class CallGraphBuilder:
    """Builds execution path trees from entry points."""

    def __init__(self, index: JavaIndex):
        self.index = index
        self.parser = index.parser

    def build_tree(
        self,
        entry_point: EntryPoint,
        entry_method_node: Node,
    ) -> CallNode:
        """Build a call tree starting from the entry point's method.

        Uses iterative BFS with a visited set (keyed on the *resolved target*)
        to prevent cycles without merging same-named methods across classes.
        """
        visited: set[str] = set()
        node_count = 0

        root = CallNode(
            method=f"{entry_point.class_name}.{entry_point.method}",
            file=entry_point.file,
            line=entry_point.line,
            class_name=entry_point.class_name,
        )
        visited.add(self._key(root.method, root.file, root.line))
        node_count += 1

        enclosing_ci = self.index.class_by_loc(
            entry_point.repo, entry_point.file, entry_point.class_name
        )

        self._expand_node(
            node=entry_method_node,
            call_node=root,
            depth=0,
            visited=visited,
            node_count=[node_count],
            enclosing_ci=enclosing_ci,
        )

        return root

    def _expand_node(
        self,
        node: Node,
        call_node: CallNode,
        depth: int,
        visited: set[str],
        node_count: list[int],
        enclosing_ci: Optional[ClassInfo],
    ):
        """Recursively expand a method body, finding invocations and resolving them."""
        if depth >= MAX_DEPTH:
            call_node.confidence = "EXTRACTED"
            return
        if node_count[0] >= MAX_NODES:
            call_node.confidence = "TRUNCATED"
            return
        if enclosing_ci is None:
            return

        body = self.parser.get_method_body(node)
        if not body:
            return

        # Type map for local receivers: method parameters + local variables,
        # so chained calls on locals resolve instead of staying INFERRED.
        local_types = {
            p["name"]: p["type"]
            for p in self.parser.get_method_parameters(node)
            if p.get("name") and p.get("type")
        }
        local_types.update(self.parser.get_local_variables(body))

        invocations = self.parser.find_method_invocations(body)

        for inv in invocations:
            if node_count[0] >= MAX_NODES:
                break

            parsed = self.parser.parse_method_invocation(inv)
            method_name = parsed["method"]
            receiver = parsed["receiver"]
            arity = len(parsed["args"])

            if not method_name:
                continue

            display_name = f"{receiver}.{method_name}" if receiver else method_name

            # Skip trivial calls (System.out.println, getters/setters, …).
            if self._is_trivial(display_name):
                continue

            resolved, ambiguous, _recv_type = self.index.resolve_call(
                enclosing_ci, receiver, method_name, arity=arity, local_types=local_types
            )

            if resolved:
                key = self._key(f"{resolved.class_simple}.{resolved.name}", resolved.file, resolved.line)
                if key in visited:
                    continue
                visited.add(key)

                child = CallNode(
                    method=display_name,
                    file=resolved.file,
                    line=resolved.line,
                    class_name=resolved.class_simple,
                    confidence="AMBIGUOUS" if ambiguous else "EXTRACTED",
                )
                call_node.children.append(child)
                node_count[0] += 1

                next_ci = self.index.class_for_method(resolved)
                if resolved.node:
                    self._expand_node(
                        node=resolved.node,
                        call_node=child,
                        depth=depth + 1,
                        visited=visited,
                        node_count=node_count,
                        enclosing_ci=next_ci,
                    )
            else:
                # Unresolved — record the call but mark it inferred (no recursion).
                if display_name in visited:
                    continue
                visited.add(display_name)
                child = CallNode(
                    method=display_name,
                    confidence="INFERRED",
                )
                call_node.children.append(child)
                node_count[0] += 1

    @staticmethod
    def _key(method: str, file: str, line: int) -> str:
        return f"{method}@{file}:{line}"

    @staticmethod
    def _is_trivial(method_name: str) -> bool:
        """Filter out calls that aren't meaningful for execution path tracing."""
        trivial_exact = {
            "println", "printf", "print",
            "toString", "hashCode", "equals", "getClass",
            "valueOf", "format",
            "stream", "collect", "toList", "map", "filter", "forEach",
            "size", "isEmpty", "contains", "indexOf",
            "add", "remove", "clear", "put", "get",
            "next", "hasNext", "iterator",
            "of", "from", "copyOf", "range", "between",
            "assertNull", "assertNotNull", "assertThat", "assertEquals",
            "debug", "info", "warn", "error", "trace",
            "findFirst", "findAny", "orElse", "orElseThrow",
            "builder", "build", "create", "newInstance",
            "values", "keySet", "entrySet",
            "asList", "singletonList", "emptyList",
            "isPresent", "isEmpty",
            "close", "flush", "shutdown", "stop",
        }
        parts = method_name.split(".")
        last = parts[-1]
        if last in trivial_exact:
            return True
        if last.startswith("get") and len(last) > 3:
            return True
        if last.startswith("set") and len(last) > 3:
            return True
        if last.startswith("is") and len(last) > 2:
            return True
        if last.startswith("has") and len(last) > 3:
            return True
        return False

    def compute_metrics(self, root: CallNode) -> dict:
        """Compute complexity metrics for a call tree."""
        depth = 0
        total_nodes = 0
        unique_files: set[str] = set()
        branch_count = 0

        def walk(node: CallNode, current_depth: int):
            nonlocal depth, total_nodes, branch_count
            depth = max(depth, current_depth)
            total_nodes += 1
            if node.file:
                unique_files.add(node.file)
            if len(node.children) > 1:
                branch_count += 1
            for child in node.children:
                walk(child, current_depth + 1)

        walk(root, 0)

        return {
            "depth": depth,
            "total_nodes": total_nodes,
            "unique_files": len(unique_files),
            "branch_count": branch_count,
        }
