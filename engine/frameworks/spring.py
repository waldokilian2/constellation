"""Spring framework entry-point detection.

Covers the Spring annotation set: REST endpoints (@*Mapping), message consumers
(@RabbitListener/@KafkaListener/@JmsListener/@SqsListener/@PulsarListener/
RocketMQ/StreamListener), event listeners (@EventListener), scheduled tasks
(@Scheduled), and WebSocket (@MessageMapping). Class-level @RequestMapping
supplies a path prefix shared by the class's REST endpoints. STOMP return-side
producers (@SendTo) are delegated to the JVM producer detector.
"""
from __future__ import annotations
from tree_sitter import Node

from .base import FrameworkHandler, ScanContext
from ..models import EntryPoint, EntryPointType
from ..producers.jvm import REST_PATH_KEYS, REST_VERB_BY_ANN, _sqs_queue_name


# annotation → (EntryPointType, channel_arg_key). "_raw" = the bare value.
CONSUMER_ANN = {
    "RabbitListener": (EntryPointType.RABBITMQ_CONSUMER, "queues"),
    "KafkaListener": (EntryPointType.KAFKA_CONSUMER, "topics"),
    "JmsListener": (EntryPointType.JMS_CONSUMER, "destination"),
    # Spring Cloud AWS 3.x: the queue name lives in queueNames (3.x named form),
    # value (2.x alias) or the bare-value form — checked in preference order.
    "SqsListener": (EntryPointType.SQS_CONSUMER, "queueNames"),
    # Spring for Apache Pulsar (topics arg; pattern-subscription forms fall
    # back to _raw/msg_type via the shared channel fallbacks below).
    "PulsarListener": (EntryPointType.PULSAR_CONSUMER, "topics"),
    # RocketMQ is a Kafka-style log broker; bucketed as Kafka consumer until a
    # dedicated type is added. Channel = topic.
    "RocketMQMessageListener": (EntryPointType.KAFKA_CONSUMER, "topic"),
    # Spring Cloud Stream listener (deprecated API). Channel = destination/_raw.
    "StreamListener": (EntryPointType.KAFKA_CONSUMER, "value"),
}

# Extra arg keys checked (in order) when the primary key is absent. Shared by
# every CONSUMER_ANN entry so alias shapes degrade gracefully instead of
# dropping to msg_type-only channels.
_CONSUMER_FALLBACK_KEYS = ("_raw", "value", "queues", "topics", "destination", "queueNames")

# Micronaut companion annotations: on a Micronaut listener class the channel
# lives in one of these METHOD annotations, not in the listener annotation's
# attributes. When a listener annotation has no channel args and one of these
# is present, this handler yields (the reactive handler owns that shape) —
# otherwise the msg_type fallback would emit a duplicate entry.
MICRONAUT_COMPANION_ANN = {"Topic", "Queue", "QueueBinding"}

REST_ANN = {
    "GetMapping": EntryPointType.REST_ENDPOINT,
    "PostMapping": EntryPointType.REST_ENDPOINT,
    "PutMapping": EntryPointType.REST_ENDPOINT,
    "DeleteMapping": EntryPointType.REST_ENDPOINT,
    "PatchMapping": EntryPointType.REST_ENDPOINT,
    "RequestMapping": EntryPointType.REST_ENDPOINT,
}

EVENT_ANN = {
    "EventListener": EntryPointType.EVENT_LISTENER,
    "TransactionalEventListener": EntryPointType.EVENT_LISTENER,
}

SCHEDULED_ANN = {"Scheduled": EntryPointType.SCHEDULED_TASK}

WEBSOCKET_ANN = {
    "MessageMapping": EntryPointType.WEBSOCKET,
    "SubscribeMapping": EntryPointType.WEBSOCKET,
}


class SpringHandler(FrameworkHandler):
    """Spring annotations → entry points; @SendTo → STOMP producers."""

    def class_entries(self, ctx: ScanContext) -> list[EntryPoint]:
        # Class-level @RocketMQMessageListener: the class implements
        # RocketMQListener.onMessage (the annotation carries topic + group,
        # the method is the standard interface contract).
        java = ctx.java
        for ann in java.get_class_annotations(ctx.class_node):
            if java.get_annotation_name(ann) != "RocketMQMessageListener":
                continue
            ep_type, key = CONSUMER_ANN["RocketMQMessageListener"]
            args = java.get_annotation_args(ann)
            chans = args.get(key) or args.get("topics") or args.get("_raw") or []
            # The handler method: onMessage (RocketMQListener contract).
            m_node = next(
                (m for m in java.find_methods(ctx.class_node)
                 if java.get_method_name(m) == "onMessage"),
                ctx.class_node,
            )
            m_name = java.get_method_name(m_node) or "onMessage"
            out: list[EntryPoint] = []
            for c in chans or ["unknown"]:
                ch = ctx.index.resolve_channel(c, ctx.ci) or "unknown"
                out.append(ctx.make_entry(m_node, m_name, ch, ep_type))
            return out
        return []

    def begin_class(self, ctx: ScanContext) -> None:
        # Class-level REST path prefix (@RequestMapping on the class).
        prefix = ""
        for ann in ctx.java.get_class_annotations(ctx.class_node):
            if ctx.java.get_annotation_name(ann) == "RequestMapping":
                args = ctx.java.get_annotation_args(ann)
                for k in REST_PATH_KEYS:
                    if k in args and args[k]:
                        prefix = args[k][0]
                        break
                break
        ctx.spring_path_prefix = prefix

    def method_entries(self, ctx, m_node, m_name, annotations, params):
        out: list[EntryPoint] = []
        prefix = getattr(ctx, "spring_path_prefix", "")
        for ann in annotations:
            ann_name = ctx.java.get_annotation_name(ann)
            out.extend(self._entries_from_annotation(ctx, ann, ann_name, m_node, m_name, params, prefix))
        return out

    def method_producers(self, ctx, m_node, m_name, annotations):
        # STOMP return-side producers (@SendTo / @SendToUser).
        return ctx.producers.stomp_producer(ctx.ci, m_node, m_name, annotations)

    # ── per-annotation entry construction ───────────────────────────

    def _entries_from_annotation(self, ctx, ann, ann_name, m_node, m_name, params, path_prefix):
        java = ctx.java
        out: list[EntryPoint] = []
        args = java.get_annotation_args(ann)

        def make(channel, ep_type, msg_type="", method_type=""):
            ch = ctx.index.resolve_channel(channel, ctx.ci) or "unknown"
            if ep_type == EntryPointType.REST_ENDPOINT and path_prefix:
                ch = ScanContext.join_rest_path(path_prefix, ch)
            if ep_type == EntryPointType.SQS_CONSUMER:
                ch = _sqs_queue_name(ch)  # URL/ARN → bare queue name
            out.append(ctx.make_entry(m_node, m_name, ch, ep_type, msg_type=msg_type, method_type=method_type))

        msg_type = params[0]["type"] if params else ""

        if ann_name in REST_ANN:
            verb = REST_VERB_BY_ANN.get(ann_name, "")
            chans: list[str] = []
            for k in REST_PATH_KEYS:
                if k in args:
                    chans = args[k]
                    break
            if not chans:
                chans = ["unknown"]
            for c in chans:
                make(c, REST_ANN[ann_name], method_type=verb)
            return out

        if ann_name in CONSUMER_ANN:
            ep_type, key = CONSUMER_ANN[ann_name]
            chans = args.get(key) or [
                v for k in _CONSUMER_FALLBACK_KEYS
                if k != key for v in (args.get(k) or [])
            ] or []
            if not chans:
                # Micronaut shape: the channel is in a companion method
                # annotation (@Topic/@Queue) — leave it to the reactive
                # handler instead of emitting a msg_type-channel duplicate.
                if any(
                    ctx.java.get_annotation_name(a) in MICRONAUT_COMPANION_ANN
                    for a in annotations
                ):
                    return out
                chans = [msg_type or "unknown"]
            for c in chans:
                make(c, ep_type, msg_type=msg_type)
            return out

        if ann_name in EVENT_ANN:
            make(msg_type or "unknown-event", EVENT_ANN[ann_name], msg_type=msg_type)
            return out

        if ann_name in SCHEDULED_ANN:
            sched = ""
            for k in ("cron", "fixedDelay", "fixedRate", "fixedDelayString", "fixedRateString"):
                if k in args and args[k]:
                    sched = args[k][0]
                    break
            make(sched or f"@Scheduled:{m_name}", SCHEDULED_ANN[ann_name])
            return out

        if ann_name in WEBSOCKET_ANN:
            chans = args.get("_raw") or args.get("value") or args.get("path") or ["unknown"]
            for c in chans:
                make(c, WEBSOCKET_ANN[ann_name])
            return out

        return out
