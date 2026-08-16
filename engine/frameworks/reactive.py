"""Quarkus / MicroProfile Reactive Messaging + Micronaut detection.

Two frameworks whose consumer annotations do NOT follow the Spring shape
(channel as an attribute of the listener annotation):

* **SmallRye Reactive Messaging** (Quarkus; also MicroProfile platforms):
  ``@Incoming("channel")`` on a method consumes; ``@Outgoing("channel")``
  produces. The logical channel maps to a physical destination in
  ``application.properties`` via ``mp.messaging.incoming.<channel>.topic`` /
  ``mp.messaging.outgoing.<channel>.topic`` (or ``topic=`` for AMQP etc.) —
  the symbol index already loads that file, so resolution is a key lookup.
  When no mapping exists the channel stays the logical name (SmallRye's
  default for the in-memory connector, and still a valid join key between
  repos that share the naming).
* **Micronaut**: listeners carry the broker on the CLASS
  (``@KafkaListener``/``@RabbitListener``/``@JmsListener``/``@MessageListener``)
  and the channel in a companion METHOD annotation (``@Topic``/``@Queue``/
  ``@QueueBinding``) or an ``@MessageMapping``-style value. The same
  annotation names collide with Spring's — there the channel is an attribute
  of the listener itself, so this handler only fires when the Spring handler
  found no channel AND the companion annotation supplies one.

All detection is deterministic: annotation names and literal values from the
AST, config keys from the indexed properties files.
"""
from __future__ import annotations
from tree_sitter import Node

from .base import FrameworkHandler, ScanContext
from ..models import EntryPoint, EntryPointType, Producer, ProducerType


# SmallRye Reactive Messaging (Quarkus / MicroProfile).
REACTIVE_INCOMING_ANN = "Incoming"
REACTIVE_OUTGOING_ANN = "Outgoing"

# Micronaut: class-level listener marker → broker entry type + the companion
# method annotations that carry the channel.
MICRONAUT_CLASS_ANN: dict[str, EntryPointType] = {
    "KafkaListener": EntryPointType.KAFKA_CONSUMER,
    "RabbitListener": EntryPointType.RABBITMQ_CONSUMER,
    "JmsListener": EntryPointType.JMS_CONSUMER,
    # Micronaut's generic @MessageListener + explicit @MessageMapping value.
    "MessageListener": EntryPointType.MESSAGE_HANDLER,
    # micronaut-mqtt: class marker + @Topic on the method.
    "MqttListener": EntryPointType.MQTT_CONSUMER,
}
# companion method annotation → arg key holding the channel ("_raw" = bare value)
MICRONAUT_CHANNEL_ANN = {
    "Topic": "_raw",
    "Queue": "_raw",
    "QueueBinding": "queue",
    "MessageMapping": "_raw",
}
# Micronaut RabbitMQ often names the queue via @Queue("orders") on the method
# inside a @RabbitListener class — the same companion shape.

# config keys for SmallRye channel → physical destination resolution, checked
# in order for both incoming and outgoing directions.
_CHANNEL_CONFIG_KEYS = ("topic", "destination", "queue", "address")

# MicroProfile Emitter field sends: @Channel("orders") Emitter<Order> e; e.send(p).
REACTIVE_EMITTER_METHODS = {"send", "sendAndAwait", "sendAsync"}


def _resolve_reactive_channel(index, ci, logical: str) -> str:
    """SmallRye logical channel → physical destination from config.

    ``mp.messaging.incoming.<ch>.topic`` / ``mp.messaging.outgoing.<ch>.topic``
    (or ``destination``/``queue``/``address`` for the AMQP/MQTT connectors).
    Falls back to the logical name — a valid SmallRye default (in-memory
    connector) and a stable join key across repos that share the naming.
    """
    for prefix in ("mp.messaging.incoming.", "mp.messaging.outgoing."):
        for key in _CHANNEL_CONFIG_KEYS:
            v = index.config.get(f"{prefix}{logical}.{key}", "")
            if v:
                return v
    return logical


class ReactiveHandler(FrameworkHandler):
    """Quarkus @Incoming/@Outgoing + Micronaut listener classes."""

    def begin_class(self, ctx: ScanContext) -> None:
        names = {
            ctx.java.get_annotation_name(a)
            for a in ctx.java.get_class_annotations(ctx.class_node)
        }
        # Micronaut listener class: remember the broker type for its methods.
        ctx.micronaut_ep_type = next(
            (MICRONAUT_CLASS_ANN[n] for n in names if n in MICRONAUT_CLASS_ANN),
            None,
        )
        # @Channel("name") Emitter fields → producer channels (Quarkus).
        ctx.reactive_channel_fields = self._channel_field_annotations(ctx)

    @staticmethod
    def _channel_field_annotations(ctx: ScanContext) -> dict:
        """``{field_name: logical_channel}`` for fields annotated
        ``@Channel("name")`` (MicroProfile reactive messaging Emitter)."""
        java = ctx.java
        out: dict[str, str] = {}

        def walk(n):
            if n.type == "field_declaration":
                ch = ""
                name = ""
                for c in n.children:
                    if c.type == "modifiers":
                        for mc in c.children:
                            if mc.type in ("annotation", "marker_annotation") \
                                    and java.get_annotation_name(mc) == "Channel":
                                args = java.get_annotation_args(mc)
                                ch = (args.get("_raw") or args.get("value") or [""])[0]
                    elif c.type == "variable_declarator":
                        for vc in c.children:
                            if vc.type == "identifier":
                                name = vc.text.decode()
                if name and ch:
                    out[name] = ch
            for c in n.children:
                walk(c)

        walk(ctx.class_node)
        return out

    def method_entries(
        self, ctx: ScanContext, m_node: Node, m_name: str,
        annotations: list[Node], params: list[dict],
    ) -> list[EntryPoint]:
        java = ctx.java
        out: list[EntryPoint] = []

        # ── SmallRye @Incoming("channel") ──
        for ann in annotations:
            if java.get_annotation_name(ann) != REACTIVE_INCOMING_ANN:
                continue
            args = java.get_annotation_args(ann)
            logical = (args.get("_raw") or args.get("value") or [""])[0]
            if not logical:
                continue
            channel = _resolve_reactive_channel(ctx.index, ctx.ci, logical)
            out.append(ctx.make_entry(
                m_node, m_name, channel, EntryPointType.REACTIVE_INCOMING,
                msg_type=params[0]["type"] if params else "",
            ))
        if out:
            return out

        # ── Micronaut: companion channel annotation inside a listener class ──
        ep_type = getattr(ctx, "micronaut_ep_type", None)
        if ep_type is not None:
            for ann in annotations:
                ann_name = java.get_annotation_name(ann)
                if ann_name not in MICRONAUT_CHANNEL_ANN:
                    continue
                key = MICRONAUT_CHANNEL_ANN[ann_name]
                chans = java.get_annotation_args(ann).get(key) or []
                for c in chans:
                    out.append(ctx.make_entry(
                        m_node, m_name, c, ep_type,
                        msg_type=params[0]["type"] if params else "",
                    ))
                if chans:
                    return out
        return out

    def body_invocation(self, ctx: ScanContext, m_name: str, inv: Node):
        """Quarkus producer: ``@Channel("orders") Emitter<Order> e; e.send(p)``.

        The channel is the @Channel field annotation's value, resolved via
        ``mp.messaging.outgoing.<channel>.topic`` config when present — the
        same key a SmallRye @Incoming consumer elsewhere resolves to, so the
        two sides link.
        """
        parsed = ctx.java.parse_method_invocation(inv)
        receiver = parsed["receiver"]
        method = parsed["method"]
        if not receiver or method not in REACTIVE_EMITTER_METHODS:
            return [], []
        ch_map = getattr(ctx, "reactive_channel_fields", {})
        logical = ch_map.get(receiver, "")
        if not logical:
            return [], []
        channel = _resolve_reactive_channel(ctx.index, ctx.ci, logical)
        return [], [Producer(
            id=f"{ctx.ci.repo}:{ctx.ci.simple_name}.{m_name}:emitter:{channel}",
            repo=ctx.ci.repo,
            type=ProducerType.UNKNOWN,  # broker-agnostic (Kafka/AMQP by config)
            channel=channel,
            method=f"{ctx.ci.simple_name}.{m_name}",
            file=ctx.ci.file,
            line=inv.start_point[0] + 1,
            message_type="",
        )]

    def method_producers(
        self, ctx: ScanContext, m_node: Node, m_name: str,
        annotations: list[Node],
    ):
        # SmallRye @Outgoing("channel") — the method's return value is sent.
        java = ctx.java
        out: list[Producer] = []
        for ann in annotations:
            if java.get_annotation_name(ann) != REACTIVE_OUTGOING_ANN:
                continue
            args = java.get_annotation_args(ann)
            logical = (args.get("_raw") or args.get("value") or [""])[0]
            if not logical:
                continue
            channel = _resolve_reactive_channel(ctx.index, ctx.ci, logical)
            out.append(Producer(
                id=f"{ctx.ci.repo}:{ctx.ci.simple_name}.{m_name}:outgoing:{channel}",
                repo=ctx.ci.repo,
                type=ProducerType.UNKNOWN,  # broker-agnostic (Kafka/AMQP/MQTT by config)
                channel=channel,
                method=f"{ctx.ci.simple_name}.{m_name}",
                file=ctx.ci.file,
                line=m_node.start_point[0] + 1,
                message_type=java.get_method_return_type(m_node) or "",
            ))
        return out
