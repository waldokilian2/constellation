"""
Framework detection layer.

Entry-point detection is split into per-framework handlers registered in
:data:`engine.frameworks.HANDLERS`. The :class:`EntryPointDetector` is now a
slim scan engine: it walks the indexed classes and delegates to each handler.
Adding a framework (Quarkus, Micronaut, …) is a new handler module + a registry
entry — the engine and the other handlers do not change.

Each handler receives a :class:`ScanContext` bundling the index, the Java AST
backend, the current class, and a shared ``make_entry`` helper, so handlers
share one canonical entry-point shape and one channel-resolution path.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from tree_sitter import Node

from ..symbol_index import SymbolIndex, TypeInfo
from ..languages import java_ast
from ..models import EntryPoint, EntryPointType


@dataclass
class ScanContext:
    """Per-class scan context shared by all framework handlers."""
    index: SymbolIndex
    java: object              # the java_ast backend module
    ci: TypeInfo
    class_node: Node
    producers: object = None  # JvmProducerDetector (set by the engine)

    def make_entry(
        self, node: Node, m_name: str, channel: str, ep_type: EntryPointType,
        msg_type: str = "", method_type: str = "",
    ) -> EntryPoint:
        """Construct an EntryPoint (channel already resolved)."""
        ch = channel or "unknown"
        suffix = f":{ch}" if ch and ch != "unknown" else ""
        return EntryPoint(
            id=f"{self.ci.repo}:{self.ci.simple_name}.{m_name}{suffix}",
            repo=self.ci.repo,
            type=ep_type,
            channel=ch,
            class_name=self.ci.simple_name,
            method=m_name,
            file=self.ci.file,
            line=node.start_point[0] + 1,
            message_type=msg_type,
            method_type=method_type,
        )

    @staticmethod
    def join_rest_path(prefix: str, channel: str) -> str:
        if channel == "unknown":
            return prefix
        if not channel.startswith("/"):
            return f"{prefix}/{channel}"
        return f"{prefix}{channel}"


class FrameworkHandler:
    """Base framework handler. Subclasses override the phases they handle.

    Every method has a safe no-op default, so a handler only implements what it
    cares about. ``begin_class`` lets a handler cache per-class context once.
    """

    def begin_class(self, ctx: ScanContext) -> None:
        """Hook to compute per-class context before methods are scanned."""

    def class_entries(self, ctx: ScanContext) -> list[EntryPoint]:
        """Class-level entry points (e.g. a JMS MessageDriven Bean)."""
        return []

    def method_entries(
        self, ctx: ScanContext, m_node: Node, m_name: str,
        annotations: list[Node], params: list[dict],
    ) -> list[EntryPoint]:
        """Method-level entry points for this framework."""
        return []

    def method_producers(
        self, ctx: ScanContext, m_node: Node, m_name: str, annotations: list[Node],
    ):
        """Method-level producers declared by annotations (e.g. STOMP @SendTo)."""
        return []

    def body_invocation(
        self, ctx: ScanContext, m_name: str, inv: Node,
    ) -> tuple[list[EntryPoint], list]:
        """Per-invocation entries/producers from a method body (e.g. Camel)."""
        return [], []
