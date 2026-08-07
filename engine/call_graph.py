"""
Call graph builder — traces execution paths from entry points.

For each entry point, walks the method body, finds all method invocations,
and resolves them to their definitions in the codebase. Recurses up to
MAX_DEPTH levels deep.

The output is a tree per entry point showing the full execution chain.
"""
from __future__ import annotations
from typing import Optional
from tree_sitter import Node

from .parser import JavaParser
from .models import EntryPoint, CallNode, ClassMethod


MAX_DEPTH = 4
MAX_NODES = 50  # Safety valve — don't build infinite trees


class CallGraphBuilder:
    """Builds execution path trees from entry points."""

    def __init__(self, methods: list[ClassMethod]):
        """
        Args:
            methods: All indexed methods in the codebase, used for resolution.
        """
        self.parser = JavaParser()
        self.method_index = self._build_index(methods)

    def _build_index(self, methods: list[ClassMethod]) -> dict[str, list[ClassMethod]]:
        """
        Build a lookup index: method_name → list of matching ClassMethods.
        Multiple classes may have a method with the same name (overloads,
        interface implementations).
        """
        index: dict[str, list[ClassMethod]] = {}
        for m in methods:
            if m.name not in index:
                index[m.name] = []
            index[m.name].append(m)
        return index

    def build_tree(
        self,
        entry_point: EntryPoint,
        entry_method_node: Node,
    ) -> CallNode:
        """
        Build a call tree starting from the entry point's method.

        Uses iterative BFS with a visited set to prevent cycles.
        """
        visited: set[str] = set()
        node_count = 0

        # Create root node
        root = CallNode(
            method=f"{entry_point.class_name}.{entry_point.method}",
            file=entry_point.file,
            line=entry_point.line,
            class_name=entry_point.class_name,
        )
        visited.add(root.method)
        node_count += 1

        # BFS worklist: (call_node, tree_node_to_attach_to, depth)
        # We process the entry method first
        self._expand_node(
            node=entry_method_node,
            call_node=root,
            depth=0,
            visited=visited,
            node_count=[node_count],
            enclosing_class=entry_point.class_name,
        )

        return root

    def _expand_node(
        self,
        node: Node,
        call_node: CallNode,
        depth: int,
        visited: set[str],
        node_count: list[int],
        enclosing_class: str,
    ):
        """
        Recursively expand a method body, finding invocations and resolving them.
        """
        if depth >= MAX_DEPTH:
            call_node.confidence = "EXTRACTED"
            return
        if node_count[0] >= MAX_NODES:
            call_node.confidence = "TRUNCATED"
            return

        body = self.parser.get_method_body(node)
        if not body:
            return

        invocations = self.parser.find_method_invocations(body)

        for inv in invocations:
            if node_count[0] >= MAX_NODES:
                break

            parsed = self.parser.parse_method_invocation(inv)
            method_name = parsed["method"]
            receiver = parsed["receiver"]

            # Build display name
            if receiver:
                display_name = f"{receiver}.{method_name}"
            else:
                display_name = method_name

            # Skip trivial calls (System.out.println, etc.)
            if self._is_trivial(display_name):
                continue

            # Skip if already visited (cycle prevention)
            if display_name in visited:
                continue

            visited.add(display_name)

            # Try to resolve to a definition
            resolved, ambiguous = self._resolve_call(
                method_name, receiver, enclosing_class
            )

            if resolved:
                child = CallNode(
                    method=display_name,
                    file=resolved.file,
                    line=resolved.line,
                    class_name=resolved.class_name,
                    confidence="AMBIGUOUS" if ambiguous else "EXTRACTED",
                )
                call_node.children.append(child)
                node_count[0] += 1

                # Recurse into resolved method
                if resolved.node:
                    self._expand_node(
                        node=resolved.node,
                        call_node=child,
                        depth=depth + 1,
                        visited=visited,
                        node_count=node_count,
                        enclosing_class=resolved.class_name,
                    )
            else:
                # Unresolved — still record the call but mark it
                child = CallNode(
                    method=display_name,
                    confidence="INFERRED",
                )
                call_node.children.append(child)
                node_count[0] += 1

    def _resolve_call(
        self,
        method_name: str,
        receiver: str,
        enclosing_class: str,
    ) -> tuple[Optional[ClassMethod], bool]:
        """
        Resolve a method call to its definition.

        Returns ``(resolved, ambiguous)`` where ``ambiguous`` is True when the
        call could not be uniquely resolved and we fell back to an arbitrary
        candidate.

        Strategy:
        1. Try receiver name as a class name (static call)
        2. Try receiver name as a field → guess its type from naming conventions
        3. Try same-class method (no receiver or 'this')
        4. Fall back to method name only (ambiguous)
        """
        candidates = self.method_index.get(method_name, [])

        if not candidates:
            return None, False

        # Strategy 1: receiver matches a class name directly
        if receiver:
            for c in candidates:
                # receiver "orderService" matches class "OrderService" or "orderService"
                if (c.class_name == receiver or
                    c.class_name.lower() == receiver.lower() or
                    self._camel_to_class(receiver) == c.class_name):
                    return c, False

        # Strategy 2: same-class method
        for c in candidates:
            if c.class_name == enclosing_class:
                return c, False

        # Strategy 3: single candidate — use it
        if len(candidates) == 1:
            return candidates[0], False

        # Strategy 4: multiple candidates, no clear winner — pick first, mark ambiguous
        return candidates[0], True

    @staticmethod
    def _camel_to_class(field_name: str) -> str:
        """Convert camelCase field name to ClassName: orderService → OrderService."""
        if not field_name:
            return field_name
        return field_name[0].upper() + field_name[1:]

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
        # Filter getters/setters (getXxx, setXxx, isXxx) and trivial methods
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
