"""
Entry point + producer detector — the generic scan engine.

The detector owns no framework knowledge. It walks the indexed classes and, for
each, delegates to the registered :mod:`engine.frameworks` handlers (Spring,
Jakarta, the extra-framework tier, Camel) and to the :mod:`engine.producers`
JVM producer detector. Channel resolution flows through the symbol index.

Adding a framework or a producer ecosystem is a new handler/producer module +
a registry entry — this module does not change.

Detection is 100% deterministic — names and arguments are read from the AST.
"""
from __future__ import annotations

from .ast_parser import ASTParser
from .languages import java_ast
from .symbol_index import SymbolIndex
from .models import EntryPoint, Producer
from .frameworks import HANDLERS, ScanContext
from .producers import JvmProducerDetector, FEIGN_ANNOTATIONS
from . import http_paths


class EntryPointDetector:
    """Detects entry points and producers by delegating to framework handlers."""

    def __init__(self, index: SymbolIndex, handlers=None):
        self.index = index
        self.parser: ASTParser = index.parser
        self.java = java_ast
        self.handlers = handlers if handlers is not None else HANDLERS
        self.producers = JvmProducerDetector(index)

    def scan(self, on_progress=None) -> tuple[list[EntryPoint], list[Producer]]:
        """Scan every indexed class for entry points and producers.

        ``on_progress(done)`` is called after each class so callers can report
        ``done/total`` progress — this phase can run minutes on large codebases
        and otherwise emits nothing until it finishes.
        """
        all_entries: list[EntryPoint] = []
        all_producers: list[Producer] = []
        for done, ci in enumerate(self.index.by_fqn.values(), 1):
            if ci.node is None:
                continue
            entries, producers = self._scan_class(ci)
            all_entries.extend(entries)
            all_producers.extend(producers)
            if on_progress:
                on_progress(done)
        return all_entries, all_producers

    def _scan_class(self, ci) -> tuple[list[EntryPoint], list[Producer]]:
        java = self.java
        class_node = ci.node
        ctx = ScanContext(index=self.index, java=java, ci=ci, class_node=class_node, producers=self.producers)

        # Declarative HTTP-client interface (@FeignClient / @HttpExchange)?
        # Its REST-annotated methods are OUTBOUND HTTP calls, never
        # server-side entry points.
        is_feign = any(
            java.get_annotation_name(a) in FEIGN_ANNOTATIONS
            for a in java.get_class_annotations(class_node)
        )

        entries: list[EntryPoint] = []
        producers: list[Producer] = []

        # Let each handler compute its per-class context.
        for handler in self.handlers:
            handler.begin_class(ctx)

        # Class-level entries (e.g. JMS MessageDriven Bean).
        for handler in self.handlers:
            entries.extend(handler.class_entries(ctx))

        for m_node in java.find_methods(class_node):
            m_name = java.get_method_name(m_node)
            if not m_name:
                continue
            self._scan_callable(ctx, ci, is_feign, entries, producers, m_node, m_name)

        # Constructors too — Axon's aggregate-creation idiom: a @CommandHandler
        # constructor consumes the command AND publishes events
        # (AggregateLifecycle.apply(new OrderCreatedEvent(…))). Entries run as
        # well (the only constructor annotation any handler table matches is
        # Axon's @CommandHandler); the call-graph/dead-code method pool stays
        # method-only (constructor semantics differ: no override/return).
        for c_node in java.find_constructors(class_node):
            c_name = "<init>"
            annotations = java.get_method_annotations(c_node)
            params = java.get_method_parameters(c_node)
            for handler in self.handlers:
                entries.extend(handler.method_entries(ctx, c_node, c_name, annotations, params))
            body = java.get_method_body(c_node)
            if body:
                local_types = java.get_local_variables(body)
                param_types = {p["name"]: p["type"] for p in params if p.get("name")}
                for inv in java.find_method_invocations(body):
                    producers.extend(self.producers.bus_producer_from_invocation(ci, c_name, inv, local_types, param_types))
                    producers.extend(self.producers.producers_from_invocation(ci, c_name, inv, local_types))
                    for handler in self.handlers:
                        _h_entries, h_producers = handler.body_invocation(ctx, c_name, inv)
                        producers.extend(h_producers)

        return entries, producers

    def _scan_callable(self, ctx, ci, is_feign, entries, producers, m_node, m_name) -> None:
        """Shared per-method scan (entries + producers + body invocations)."""
        java = self.java
        annotations = java.get_method_annotations(m_node)
        params = java.get_method_parameters(m_node)

        # Feign client interface: every REST-annotated method is an outbound
        # HTTP call — never a server-side entry point.
        if is_feign:
            producers.extend(self.producers.feign_calls(ci, m_node, m_name, annotations))
            return

        # Per-framework method-level entries + annotation producers (STOMP).
        for handler in self.handlers:
            entries.extend(handler.method_entries(ctx, m_node, m_name, annotations, params))
            producers.extend(handler.method_producers(ctx, m_node, m_name, annotations))

        # Producers within the method body (type-based): message producers,
        # sync HTTP calls (method-based clients), fluent HTTP clients
        # (WebClient/RestClient/Builder), in-house bus facades
        # (bus.send(payload)), and per-framework body handlers (Camel).
        body = java.get_method_body(m_node)
        if body:
            apache_map = self.producers.apache_request_map(body)  # built once per method
            local_types = java.get_local_variables(body)
            param_types = {p["name"]: p["type"] for p in params if p.get("name")}
            producers.extend(self.producers.fluent_http_calls(ci, m_name, body))
            for inv in java.find_method_invocations(body):
                producers.extend(self.producers.bus_producer_from_invocation(ci, m_name, inv, local_types, param_types))
                producers.extend(self.producers.producers_from_invocation(ci, m_name, inv, local_types))
                producers.extend(self.producers.http_calls_from_invocation(ci, m_name, inv, apache_map))
                for handler in self.handlers:
                    h_entries, h_producers = handler.body_invocation(ctx, m_name, inv)
                    entries.extend(h_entries)
                    producers.extend(h_producers)

    # ── retained static helpers (delegates; some tests/callers use them) ──

    @staticmethod
    def _normalize_http_path(path: str) -> str:
        """Canonical form for HTTP path matching.

        Delegates to :func:`engine.http_paths.normalize_http_path`; kept as a
        staticmethod so existing callers (incl. the test suite) are unaffected.
        """
        return http_paths.normalize_http_path(path)

    @staticmethod
    def _strip_http_origin(channel: str) -> str:
        return http_paths.strip_http_origin(channel)
