"""Apache Camel (RouteBuilder DSL) detection.

A class extending ``RouteBuilder`` declares routes via ``from(...)``/``to(...)``.
The endpoint URI scheme selects the broker; the remainder is the channel. Only
real broker schemes produce edges (internal/utility schemes are skipped).
"""
from __future__ import annotations
from tree_sitter import Node

from .base import FrameworkHandler, ScanContext
from ..models import EntryPoint, EntryPointType, Producer, ProducerType

# Classes whose subclasses declare Camel routes.
CAMEL_ROUTE_BASES = {"RouteBuilder", "RoutesBuilder", "RouteConfigurationBuilder"}
CAMEL_CONSUME_METHODS = {"from"}
# Produce-side routing methods (send a message to the endpoint). NOTE: enrich /
# pollEnrich request-reply/poll FROM the endpoint (consume-side) and are
# intentionally NOT here — they would invert link direction.
CAMEL_PRODUCE_METHODS = {"to", "toD", "toF", "wireTap"}
# Endpoint scheme → (consumer EntryPointType, producer ProducerType). Schemes
# not listed here (direct, seda, file, log, http, ...) are skipped to keep the
# graph focused on cross-service message edges.
CAMEL_SCHEME_TYPE: dict[str, tuple[EntryPointType, ProducerType]] = {
    "kafka":    (EntryPointType.KAFKA_CONSUMER,    ProducerType.KAFKA_PRODUCER),
    "rabbitmq": (EntryPointType.RABBITMQ_CONSUMER, ProducerType.RABBITMQ_PRODUCER),
    "jms":      (EntryPointType.JMS_CONSUMER,      ProducerType.JMS_PRODUCER),
    "sqs":      (EntryPointType.SQS_CONSUMER,      ProducerType.UNKNOWN),
    "aws2-sqs": (EntryPointType.SQS_CONSUMER,      ProducerType.UNKNOWN),
    "aws-sqs":  (EntryPointType.SQS_CONSUMER,      ProducerType.UNKNOWN),
}


class CamelHandler(FrameworkHandler):
    """Camel ``from()``/``to()`` on a RouteBuilder subclass → entries/producers."""

    def begin_class(self, ctx: ScanContext) -> None:
        ctx.is_camel = any(s in CAMEL_ROUTE_BASES for s in ctx.ci.supertypes)

    def body_invocation(self, ctx: ScanContext, m_name: str, inv: Node):
        if not getattr(ctx, "is_camel", False):
            return [], []
        parsed = ctx.java.parse_method_invocation(inv)
        method = parsed["method"]
        if method not in CAMEL_CONSUME_METHODS and method not in CAMEL_PRODUCE_METHODS:
            return [], []
        uri = parsed["args"][0] if parsed["args"] else ""
        # Only literal endpoint URIs with a scheme (kafka:topic, jms:queue:x, …).
        if not isinstance(uri, str) or ":" not in uri:
            return [], []
        scheme, channel, entry_type, prod_type = self._parse_camel_endpoint(uri)
        if scheme not in CAMEL_SCHEME_TYPE or not channel:
            return [], []
        resolved = ctx.index.resolve_channel(channel, ctx.ci) or channel
        if method in CAMEL_CONSUME_METHODS:
            return [ctx.make_entry(inv, m_name, resolved, entry_type)], []
        return [], [Producer(
            id=f"{ctx.ci.repo}:{ctx.ci.simple_name}.{m_name}:{method}:{resolved}",
            repo=ctx.ci.repo,
            type=prod_type,
            channel=resolved,
            method=f"{ctx.ci.simple_name}.{m_name}",
            file=ctx.ci.file,
            line=inv.start_point[0] + 1,
            message_type="",
        )]

    # ── endpoint parsing ────────────────────────────────────────────

    @staticmethod
    def _parse_camel_endpoint(uri: str):
        main = uri.split("?", 1)[0]
        if ":" not in main:
            return "", main, EntryPointType.UNKNOWN, ProducerType.UNKNOWN
        scheme, rest = main.split(":", 1)
        types = CAMEL_SCHEME_TYPE.get(scheme)
        entry_type = types[0] if types else EntryPointType.UNKNOWN
        prod_type = types[1] if types else ProducerType.UNKNOWN
        channel = CamelHandler._camel_channel(scheme, rest)
        return scheme, channel, entry_type, prod_type

    @staticmethod
    def _camel_channel(scheme: str, rest: str) -> str:
        """Channel name from the URI remainder (query string already stripped)."""
        rest = rest.strip()
        if scheme == "jms":
            low = rest.lower()
            for p in ("temp-topic:", "temp-queue:", "queue:", "topic:"):
                if low.startswith(p):
                    rest = rest[len(p):]
                    break
            return rest.rsplit("/", 1)[-1]
        # kafka / rabbitmq / sqs / generic: take the last path segment. For
        # rabbitmq://host[:port]/exchange this yields the exchange name.
        if "//" in rest:
            rest = rest.split("//", 1)[1]
        return rest.rsplit("/", 1)[-1]
