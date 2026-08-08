"""
Entry point + producer detector for Java Spring applications.

Driven by the type-aware :mod:`engine.java_index` symbol table:

  * **Producers** are matched by the *declared type* of the receiver field
    (``KafkaTemplate``, ``RabbitTemplate``, ``JmsTemplate``,
    ``ApplicationEventPublisher``, ``StreamBridge``) — not by variable name,
    which avoided false positives like any ``template.send(...)``.
  * **Entry points** cover the Spring annotation set, including
    ``@Scheduled`` and ``@MessageMapping`` (WebSocket/STOMP), plus RocketMQ
    ``@RocketMQMessageListener``.
  * **Channel names** are resolved through the index: string literals,
    ``Class.CONST`` / bare constant references, ``${...}`` placeholders (via
    ``application.properties``/``.yml``), and ``#{...}`` SpEL (preserved as a
    dynamic marker).

Detection is 100% deterministic — names and arguments are read from the AST.
"""
from __future__ import annotations
from typing import Optional
from tree_sitter import Node

from .parser import JavaParser
from .java_index import (
    JavaIndex,
    PRODUCER_TYPES,
    EVENT_PUBLISHER_TYPES,
    HTTP_CLIENT_TYPES,
)
from .models import (
    EntryPoint,
    EntryPointType,
    Producer,
    ProducerType,
)


# ── Sync HTTP client detection ────────────────────────────────────
# Feign client interfaces: outbound HTTP calls, NOT REST entry points.
FEIGN_ANNOTATION = "FeignClient"
# Method-level REST annotations on a Feign client interface → the HTTP verb
# of each outbound call.
REST_VERB_BY_ANN = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
# RestTemplate method → HTTP verb (exchange/execute take the verb as an arg).
HTTP_METHOD_BY_TEMPLATE_CALL = {
    "getForObject": "GET", "getForEntity": "GET",
    "postForObject": "POST", "postForEntity": "POST",
    "put": "PUT", "patchForObject": "PATCH", "delete": "DELETE",
}


# ── Annotation → (EntryPointType, channel_arg_key) ─────────────────
# channel_arg_key selects which annotation argument holds the channel; the
# value is always extracted as a list (arrays like topics = {"a","b"} produce
# one entry point per element). "_raw" = the bare value; "_param_type" = the
# event type taken from the method's first parameter.

CONSUMER_ANN = {
    "RabbitListener": (EntryPointType.RABBITMQ_CONSUMER, "queues"),
    "KafkaListener": (EntryPointType.KAFKA_CONSUMER, "topics"),
    "JmsListener": (EntryPointType.JMS_CONSUMER, "destination"),
    "SqsListener": (EntryPointType.SQS_CONSUMER, "_raw"),
    # RocketMQ is a Kafka-style log broker; bucketed as Kafka consumer until a
    # dedicated type is added. Channel = topic.
    "RocketMQMessageListener": (EntryPointType.KAFKA_CONSUMER, "topic"),
    # Spring Cloud Stream listener (deprecated API). Channel = destination/_raw.
    "StreamListener": (EntryPointType.KAFKA_CONSUMER, "value"),
}

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

SCHEDULED_ANN = {
    "Scheduled": EntryPointType.SCHEDULED_TASK,
}

WEBSOCKET_ANN = {
    "MessageMapping": EntryPointType.WEBSOCKET,
    "SubscribeMapping": EntryPointType.WEBSOCKET,
}

# REST path keys, in preference order (handles @*Mapping(value=..., path=...)).
REST_PATH_KEYS = ("value", "path", "_raw")

# ── Java EE / Jakarta EE ───────────────────────────────────────────
# JAX-RS verbs (method level); the path comes from @Path (class + method).
JAXRS_VERB_ANN = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
# Java EE WebSocket handler methods on a @ServerEndpoint class.
EE_WS_HANDLER_ANN = {"OnMessage", "OnOpen", "OnClose", "OnError"}
# EJB timer methods.
EJB_SCHEDULE_ANN = {"Schedule", "Schedules"}


# ── Field-type → producer mapping ──────────────────────────────────
# ProducerType label per framework field type. The methods that count are in
# java_index.PRODUCER_TYPES; this just labels the edge for display/linking.
PRODUCER_TYPE_LABEL: dict[str, ProducerType] = {
    "KafkaTemplate": ProducerType.KAFKA_PRODUCER,
    "RabbitTemplate": ProducerType.RABBITMQ_PRODUCER,
    "AmqpTemplate": ProducerType.RABBITMQ_PRODUCER,
    "JmsTemplate": ProducerType.JMS_PRODUCER,
    "ApplicationEventPublisher": ProducerType.EVENT_PUBLISHER,
    "StreamBridge": ProducerType.UNKNOWN,  # links by channel; broker-agnostic
}


def _walk_nodes(node) -> iter:
    """Depth-first walk of a tree-sitter node's descendants."""
    if node is None:
        return
    stack = list(node.children)
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(cur.children))


class EntryPointDetector:
    """Detects entry points and producers across an indexed codebase."""

    def __init__(self, index: JavaIndex):
        self.index = index
        self.parser = index.parser

    def scan(self) -> tuple[list[EntryPoint], list[Producer]]:
        all_entries: list[EntryPoint] = []
        all_producers: list[Producer] = []
        for ci in self.index.by_fqn.values():
            if ci.node is None:
                continue
            entries, producers = self._scan_class(ci)
            all_entries.extend(entries)
            all_producers.extend(producers)
        return all_entries, all_producers

    def _scan_class(self, ci) -> tuple[list[EntryPoint], list[Producer]]:
        class_node = ci.node

        # Class-level REST path prefix (@RequestMapping on the class).
        path_prefix = ""
        for ann in self.parser.get_class_annotations(class_node):
            if self.parser.get_annotation_name(ann) == "RequestMapping":
                args = self.parser.get_annotation_args(ann)
                for k in REST_PATH_KEYS:
                    if k in args and args[k]:
                        path_prefix = args[k][0]
                        break
                break

        # Feign client interface? Its REST-annotated methods are OUTBOUND HTTP
        # calls, never server-side entry points (see _feign_calls).
        class_anns = self.parser.get_class_annotations(class_node)
        is_feign = any(
            self.parser.get_annotation_name(a) == FEIGN_ANNOTATION
            for a in class_anns
        )

        # Java EE class context: @Path prefix, @ServerEndpoint path, @MessageDriven.
        jaxrs_prefix = ""
        ee_ws_path = ""
        for ann in class_anns:
            name = self.parser.get_annotation_name(ann)
            args = self.parser.get_annotation_args(ann)
            if name == "Path":
                for k in REST_PATH_KEYS:
                    if k in args and args[k]:
                        jaxrs_prefix = args[k][0]
                        break
            elif name == "ServerEndpoint":
                for k in REST_PATH_KEYS:
                    if k in args and args[k]:
                        ee_ws_path = args[k][0]
                        break

        entries: list[EntryPoint] = []
        producers: list[Producer] = []

        # Class-level entry: JMS MessageDriven Bean (MDB) consumer.
        mdb = self._mdb_entry(ci, class_node)
        if mdb:
            entries.append(mdb)

        for m_node in self.parser.find_methods(class_node):
            m_name = self.parser.get_method_name(m_node)
            if not m_name:
                continue
            annotations = self.parser.get_method_annotations(m_node)
            params = self.parser.get_method_parameters(m_node)

            # Feign client interface: every REST-annotated method is an
            # outbound HTTP call — never a server-side entry point.
            if is_feign:
                producers.extend(self._feign_calls(ci, m_node, m_name, annotations))
                continue

            # Spring-style annotation entries.
            for ann in annotations:
                ann_name = self.parser.get_annotation_name(ann)
                new_entries = self._entries_from_annotation(
                    ci, ann, ann_name, m_node, m_name, params, path_prefix
                )
                entries.extend(new_entries)

            # Java EE entries (JAX-RS, CDI @Observes, EJB @Schedule, WebSocket).
            entries.extend(self._ee_entries(
                ci, m_node, m_name, annotations,
                self.parser.get_method_params_annotated(m_node),
                jaxrs_prefix, ee_ws_path,
            ))

            # Producers within the method body (type-based).
            body = self.parser.get_method_body(m_node)
            if body:
                for inv in self.parser.find_method_invocations(body):
                    producers.extend(self._producers_from_invocation(ci, m_name, inv))
                    producers.extend(self._http_calls_from_invocation(ci, m_name, inv))

        return entries, producers

    def _make_entry(self, ci, node, m_name, channel, ep_type, msg_type="") -> EntryPoint:
        """Construct an EntryPoint (channel already resolved)."""
        ch = channel or "unknown"
        suffix = f":{ch}" if ch and ch != "unknown" else ""
        return EntryPoint(
            id=f"{ci.repo}:{ci.simple_name}.{m_name}{suffix}",
            repo=ci.repo,
            type=ep_type,
            channel=ch,
            class_name=ci.simple_name,
            method=m_name,
            file=ci.file,
            line=node.start_point[0] + 1,
            message_type=msg_type,
        )

    def _ee_entries(self, ci, m_node, m_name, annotations, params_ann, jaxrs_prefix, ee_ws_path):
        """Java EE / Jakarta EE method-level entries (JAX-RS, CDI, EJB, WebSocket)."""
        out: list[EntryPoint] = []
        ann_names = [self.parser.get_annotation_name(a) for a in annotations]

        # JAX-RS: method carries an HTTP-verb annotation; path = class @Path + method @Path.
        verb = next((n for n in ann_names if n in JAXRS_VERB_ANN), None)
        if verb:
            method_path = ""
            for a in annotations:
                if self.parser.get_annotation_name(a) == "Path":
                    aargs = self.parser.get_annotation_args(a)
                    method_path = (aargs.get("_raw") or [""])[0]
                    break
            if method_path:
                channel = self._join_rest_path(jaxrs_prefix, method_path)
            else:
                channel = jaxrs_prefix or "unknown"
            out.append(self._make_entry(ci, m_node, m_name, self.index.resolve_channel(channel, ci), EntryPointType.REST_ENDPOINT))
            return out

        # CDI event observer: a parameter annotated @Observes → channel = event type.
        for p in params_ann:
            if "Observes" in p.get("annotations", []):
                evt = p["type"] or "unknown-event"
                out.append(self._make_entry(ci, m_node, m_name, evt, EntryPointType.EVENT_LISTENER, msg_type=evt))
                return out

        # EJB timer: @Schedule / @Schedules.
        if any(n in EJB_SCHEDULE_ANN for n in ann_names):
            out.append(self._make_entry(ci, m_node, m_name, f"@Schedule:{m_name}", EntryPointType.SCHEDULED_TASK))
            return out

        # Java EE WebSocket: handler methods on a @ServerEndpoint class.
        if ee_ws_path and any(n in EE_WS_HANDLER_ANN for n in ann_names):
            out.append(self._make_entry(ci, m_node, m_name, ee_ws_path, EntryPointType.WEBSOCKET))
            return out

        return out

    def _mdb_entry(self, ci, class_node):
        """JMS MessageDriven Bean: one consumer whose channel is the activationConfig destination."""
        for ann in self.parser.get_class_annotations(class_node):
            if self.parser.get_annotation_name(ann) != "MessageDriven":
                continue
            destination = ""
            for nested in self.parser.find_nested_annotations(ann):
                if self.parser.get_annotation_name(nested) != "ActivationConfigProperty":
                    continue
                props = self.parser.get_annotation_args(nested)
                pname = (props.get("propertyName") or [""])[0]
                pvalue = (props.get("propertyValue") or [""])[0]
                if pname == "destination" and pvalue:
                    destination = pvalue
            channel = self.index.resolve_channel(destination or "unknown", ci)
            # MDB implements MessageListener.onMessage — that is the handler.
            return self._make_entry(ci, class_node, "onMessage", channel, EntryPointType.JMS_CONSUMER, msg_type="javax.jms.Message")
        return None

    # ── entry points ───────────────────────────────────────────────

    def _entries_from_annotation(self, ci, ann, ann_name, m_node, m_name, params, path_prefix):
        out: list[EntryPoint] = []
        args = self.parser.get_annotation_args(ann)

        def make(channel: str, ep_type: EntryPointType, msg_type: str = "") -> None:
            ch = self.index.resolve_channel(channel, ci) or "unknown"
            if ep_type == EntryPointType.REST_ENDPOINT and path_prefix:
                ch = self._join_rest_path(path_prefix, ch)
            out.append(EntryPoint(
                id=f"{ci.repo}:{ci.simple_name}.{m_name}" + (f":{ch}" if ch and ch != "unknown" else ""),
                repo=ci.repo,
                type=ep_type,
                channel=ch,
                class_name=ci.simple_name,
                method=m_name,
                file=ci.file,
                line=m_node.start_point[0] + 1,
                message_type=msg_type or (params[0]["type"] if params else ""),
            ))

        msg_type = params[0]["type"] if params else ""

        if ann_name in REST_ANN:
            # REST: value/path keys (may be an array → one endpoint each).
            chans: list[str] = []
            for k in REST_PATH_KEYS:
                if k in args:
                    chans = args[k]
                    break
            if not chans:
                chans = ["unknown"]
            for c in chans:
                make(c, REST_ANN[ann_name])
            return out

        if ann_name in CONSUMER_ANN:
            ep_type, key = CONSUMER_ANN[ann_name]
            chans = args.get(key) or args.get("_raw") or []
            if not chans:
                chans = [msg_type or "unknown"]
            for c in chans:
                make(c, ep_type, msg_type=msg_type)
            return out

        if ann_name in EVENT_ANN:
            # Channel = event type from the first parameter.
            make(msg_type or "unknown-event", EVENT_ANN[ann_name], msg_type=msg_type)
            return out

        if ann_name in SCHEDULED_ANN:
            # Channel = the cron/fixedDelay/fixedRate value, else a stable label.
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

    @staticmethod
    def _join_rest_path(prefix: str, channel: str) -> str:
        if channel == "unknown":
            return prefix
        if not channel.startswith("/"):
            return f"{prefix}/{channel}"
        return f"{prefix}{channel}"

    # ── sync HTTP calls ───────────────────────────────────────────

    def _feign_calls(self, ci, m_node, m_name, annotations) -> list[Producer]:
        """Outbound HTTP calls declared by a @FeignClient interface method.

        Channel = base url (from @FeignClient url attr, resolved) + method path.
        """
        out: list[Producer] = []
        base = ""
        for ann in self.parser.get_class_annotations(ci.node):
            if self.parser.get_annotation_name(ann) != FEIGN_ANNOTATION:
                continue
            args = self.parser.get_annotation_args(ann)
            raw_url = (args.get("url") or args.get("value") or [""])[0]
            if raw_url:
                base = self.index.resolve_channel(raw_url, ci)
            break
        for ann in annotations:
            ann_name = self.parser.get_annotation_name(ann)
            if ann_name not in REST_VERB_BY_ANN:
                continue
            args = self.parser.get_annotation_args(ann)
            paths: list[str] = []
            for k in REST_PATH_KEYS:
                if k in args:
                    paths = args[k]
                    break
            for p in (paths or ["unknown"]):
                path = self._join_rest_path(base, p) if base else p
                path = self._strip_http_origin(path) or "/"
                out.append(Producer(
                    id=f"{ci.repo}:{ci.simple_name}.{m_name}:http:{p}",
                    repo=ci.repo,
                    type=ProducerType.HTTP_CALL,
                    channel=path,
                    method=f"{ci.simple_name}.{m_name}",
                    file=ci.file,
                    line=m_node.start_point[0] + 1,
                    message_type=REST_VERB_BY_ANN[ann_name],
                ))
        return out

    def _http_calls_from_invocation(self, ci, m_name, inv) -> list[Producer]:
        """RestTemplate/WebClient/RestClient/HttpClient/OkHttp/JAX-RS sync calls."""
        parsed = self.parser.parse_method_invocation(inv)
        receiver = parsed["receiver"]
        method_called = parsed["method"]
        if not receiver:
            return []
        field_type = self.index.field_type(ci, receiver)
        if not field_type or field_type not in HTTP_CLIENT_TYPES:
            return []
        if method_called not in HTTP_CLIENT_TYPES[field_type]:
            return []

        verb = HTTP_METHOD_BY_TEMPLATE_CALL.get(method_called, "")
        if method_called == "exchange":
            # RestTemplate.exchange(HttpMethod.GET, ...) or (url, HttpMethod, ...)
            for a in parsed["args"]:
                up = str(a).upper()
                if up in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                    verb = up
                    break

        path = self._extract_http_path(inv, field_type)
        if not path:
            return []
        channel = self.index.resolve_channel(path, ci) or path
        channel = self._strip_http_origin(channel)
        if not channel or channel == "unknown":
            return []
        return [Producer(
            id=f"{ci.repo}:{ci.simple_name}.{m_name}:http:{channel}",
            repo=ci.repo,
            type=ProducerType.HTTP_CALL,
            channel=channel,
            method=f"{ci.simple_name}.{m_name}",
            file=ci.file,
            line=inv.start_point[0] + 1,
            message_type=verb,
        )]

    def _extract_http_path(self, invocation: Node, field_type: str) -> str:
        """First URL-ish argument: direct string for RestTemplate; nested
        .uri(/.url(/.path(/.target( calls for fluent/builder clients."""
        parsed = self.parser.parse_method_invocation(invocation)
        for a in parsed["args"]:
            if isinstance(a, str) and (a.startswith("/") or a.startswith("http") or "${" in a or "#{" in a):
                return a
        # Builder/fluent style: walk nested invocations for a URI-bearing call.
        for child in _walk_nodes(invocation):
            if child.type != "method_invocation":
                continue
            inner = self.parser.parse_method_invocation(child)
            if inner["method"] in ("uri", "url", "target", "path") and inner["args"]:
                a = inner["args"][0]
                if isinstance(a, str) and (a.startswith("/") or a.startswith("http") or "${" in a or "#{" in a):
                    return a
        return ""

    @staticmethod
    def _strip_http_origin(channel: str) -> str:
        """'http://host:port/api/x' → '/api/x'; '/api/x' stays as-is."""
        for scheme in ("https://", "http://"):
            if channel.startswith(scheme):
                rest = channel[len(scheme):]
                slash = rest.find("/")
                return rest[slash:] if slash >= 0 else "/"
        return channel

    @staticmethod
    def _normalize_http_path(path: str) -> str:
        """Canonical form for HTTP path matching: replace {id} and literal-id
        segments with a placeholder. '/api/orders/123' == '/api/orders/{id}'."""
        segs = path.strip("/").split("/")
        norm = []
        for s in segs:
            if not s:
                continue
            if "{" in s or "}" in s:
                norm.append("{p}")
            elif s.isdigit() or (len(s) == 36 and "-" in s):  # numeric id / uuid
                norm.append("{p}")
            else:
                norm.append(s)
        return "/" + "/".join(norm)

    # ── producers ──────────────────────────────────────────────────

    def _producers_from_invocation(self, ci, m_name, inv) -> list[Producer]:
        parsed = self.parser.parse_method_invocation(inv)
        receiver = parsed["receiver"]
        method_called = parsed["method"]
        args = parsed["args"]
        if not receiver:
            return []

        # Resolve the receiver's declared type and match against framework types.
        field_type = self.index.field_type(ci, receiver)
        if not field_type or field_type not in PRODUCER_TYPES:
            return []
        if method_called not in PRODUCER_TYPES[field_type]:
            return []

        prod_type = PRODUCER_TYPE_LABEL.get(field_type, ProducerType.UNKNOWN)

        # Determine the channel.
        channel = args[0] if args else ""
        message_type = ""
        if field_type in EVENT_PUBLISHER_TYPES:
            channel = self._extract_event_type_from_args(inv) or (args[0] if args else "")
            message_type = channel
        else:
            message_type = self._extract_message_type_from_invocation(inv)

        resolved = self.index.resolve_channel(channel, ci) if channel else "unknown"

        return [Producer(
            id=f"{ci.repo}:{ci.simple_name}.{m_name}:{method_called}:{receiver}",
            repo=ci.repo,
            type=prod_type,
            channel=resolved,
            method=f"{ci.simple_name}.{m_name}",
            file=ci.file,
            line=inv.start_point[0] + 1,
            message_type=message_type,
        )]

    def _extract_event_type_from_args(self, invocation: Node) -> str:
        """For publishEvent(new OrderCreatedEvent(...)), extract 'OrderCreatedEvent'."""
        for child in invocation.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "object_creation_expression":
                        for oc in arg.children:
                            if oc.type in ("type_identifier", "scoped_identifier"):
                                return self.parser.last_seg(oc)
        return ""

    def _extract_message_type_from_invocation(self, invocation: Node) -> str:
        """Best-effort message type from invocation arguments (uppercase token)."""
        inv = self.parser.parse_method_invocation(invocation)
        for arg in inv.get("args", []):
            if arg and arg[0].isupper():
                # Strip a Class. prefix if present.
                return arg.rsplit(".", 1)[-1]
        return ""
