"""
JVM producer + sync-HTTP-call detection.

Tables and per-invocation logic moved out of the entry detector so producer
knowledge is isolated and swappable. Behaviour is unchanged from the previous
inlined implementation.
"""
from __future__ import annotations
from typing import Optional
from tree_sitter import Node

from ..languages import java_ast
from ..symbol_index import SymbolIndex, TypeInfo
from ..models import Producer, ProducerType
from .. import http_paths


# ── Producer field-type tables ─────────────────────────────────────────────
# Matched by the *declared field type*, not the variable name.

# field type → set of methods that produce (the channel is args[0]).
PRODUCER_TYPES: dict[str, set[str]] = {
    "KafkaTemplate": {"send"},
    "RabbitTemplate": {"convertAndSend", "convertSendAndReceive", "send"},
    "AmqpTemplate": {"convertAndSend", "convertSendAndReceive", "send"},
    "JmsTemplate": {"convertAndSend", "convertSendAndReceive", "send"},
    "ApplicationEventPublisher": {"publishEvent"},
    "StreamBridge": {"send"},          # Spring Cloud Stream
    "PulsarTemplate": {"send", "sendAsync"},   # Apache Pulsar (topic = first arg)
    "Connection": {"publish"},         # NATS / nats.java (subject = first arg)
    # Apache RocketMQ (rocketmq-spring): destination as "topic:tag" or "topic".
    "RocketMQTemplate": {"syncSend", "asyncSend", "sendOneway", "syncSendOrderly", "convertAndSend"},
    # Spring Cloud AWS 3.x / AWS SDK v2 (queue URL, name, or ARN = first arg).
    "SqsTemplate": {"send"},
    "SqsClient": {"sendMessage"},
    # AWS SDK v1 (queue URL/name = first arg).
    "AmazonSQS": {"sendMessage", "sendMessageBatch"},
    "AmazonSQSAsync": {"sendMessage", "sendMessageBatch"},
    # SNS (topic ARN or name = first arg). Spring Cloud AWS SnsTemplate offers
    # both send() and convertAndSend().
    "SnsTemplate": {"send", "convertAndSend"},
    "SnsClient": {"publish"},
    "AmazonSNS": {"publish", "publishBatch"},
    "AmazonSNSAsync": {"publish", "publishBatch"},
    # JMS 2.0 simplified API: context.createProducer().send(dest, payload).
    "JMSProducer": {"send"},
    # Eclipse Paho MQTT (topic = first arg).
    "MqttClient": {"publish"},
    "MqttAsyncClient": {"publish"},
    # Spring Data Redis pub/sub (channel = first arg).
    "RedisTemplate": {"convertAndSend"},
    "StringRedisTemplate": {"convertAndSend"},
}

# Payload-routed dispatch APIs (Axon-style): the destination is derived from
# the payload's type at runtime, exactly like the in-house bus facade. The
# payload type (FQN when resolvable) IS the channel — args[0] is the payload,
# never a destination string.
PAYLOAD_ROUTED_TYPES: dict[str, set[str]] = {
    "CommandGateway": {"send", "sendAndWait", "sendAndWaitForMessage"},
    "CommandBus": {"dispatch"},
    "EventGateway": {"publish"},
    "EventBus": {"publish"},
    "QueryGateway": {"query", "scatterGather", "subscriptionQuery"},
    "QueryBus": {"query"},
}

# gRPC client stubs: the generated type name encodes both the service and the
# stub flavor — OrderServiceGrpc.OrderServiceBlockingStub → service
# "OrderService". Channel = "/<Service>/<method>", the exact format the
# server-side detector emits for *ImplBase overrides, so the two link for free.
GRPC_STUB_SUFFIXES = ("BlockingStub", "FutureStub", "Stub")

EVENT_PUBLISHER_TYPES = {"ApplicationEventPublisher"}

# field-type → HTTP client method set. RestTemplate is method-based; WebClient
# is fluent (get().uri(...).retrieve()) so the fluent entry calls are matched
# and the URI dug out of the nested .uri(...) invocation. Feign is NOT here —
# Feign client interfaces are handled via @FeignClient in feign_calls().
HTTP_CLIENT_TYPES: dict[str, set[str]] = {
    "RestTemplate": {
        "getForObject", "getForEntity", "postForObject", "postForEntity",
        "put", "patchForObject", "delete", "exchange", "execute",
    },
    "WebClient": {"get", "post", "put", "patch", "delete", "exchange"},
    "RestClient": {"get", "post", "put", "patch", "delete"},  # Spring 6.1 fluent
    "HttpClient": {"send", "sendAsync", "execute"},   # java.net.http (send*) OR apache (execute)
    "OkHttpClient": {"newCall"},           # Request.Builder().url(...) — URI from nested builder
    "Client": {"target", "invoke"},        # JAX-RS — URI from .target("...")
    "WebTarget": {"request", "path"},
    "CloseableHttpClient": {"execute"},
    "DefaultCloseableHttpClient": {"execute"},
    "AsyncHttpClient": {
        "prepareGet", "preparePost", "preparePut", "preparePatch",
        "prepareDelete", "prepareHead", "prepareOptions", "execute",
    },
    "DefaultAsyncHttpClient": {
        "prepareGet", "preparePost", "preparePut", "preparePatch",
        "prepareDelete", "prepareHead", "prepareOptions", "execute",
    },
}

# field type → ProducerType label for the edge (display/linking only).
PRODUCER_TYPE_LABEL: dict[str, ProducerType] = {
    "KafkaTemplate": ProducerType.KAFKA_PRODUCER,
    "RabbitTemplate": ProducerType.RABBITMQ_PRODUCER,
    "AmqpTemplate": ProducerType.RABBITMQ_PRODUCER,
    "JmsTemplate": ProducerType.JMS_PRODUCER,
    "ApplicationEventPublisher": ProducerType.EVENT_PUBLISHER,
    "StreamBridge": ProducerType.UNKNOWN,  # links by channel; broker-agnostic
    "PulsarTemplate": ProducerType.PULSAR_PRODUCER,
    "Connection": ProducerType.NATS_PRODUCER,  # NATS publish(subject, …)
    "RocketMQTemplate": ProducerType.KAFKA_PRODUCER,  # Kafka-style log broker
    "SqsTemplate": ProducerType.SQS_PRODUCER,
    "SqsClient": ProducerType.SQS_PRODUCER,
    "AmazonSQS": ProducerType.SQS_PRODUCER,
    "AmazonSQSAsync": ProducerType.SQS_PRODUCER,
    "SnsTemplate": ProducerType.SNS_PRODUCER,
    "SnsClient": ProducerType.SNS_PRODUCER,
    "AmazonSNS": ProducerType.SNS_PRODUCER,
    "AmazonSNSAsync": ProducerType.SNS_PRODUCER,
    "JMSProducer": ProducerType.JMS_PRODUCER,
    "MqttClient": ProducerType.UNKNOWN,          # broker-agnostic topic
    "MqttAsyncClient": ProducerType.UNKNOWN,
    "RedisTemplate": ProducerType.UNKNOWN,       # Redis pub/sub channel
    "StringRedisTemplate": ProducerType.UNKNOWN,
}


# ── Sync HTTP client detection tables ─────────────────────────────────────

# Declarative HTTP client interfaces: outbound HTTP calls, NOT REST entry
# points. Spring Cloud OpenFeign (@FeignClient) and Spring 6 declarative HTTP
# interfaces (@HttpExchange, the built-in Feign alternative proxied via
# WebClient/RestTemplate) share the identical detection shape.
FEIGN_ANNOTATIONS = {"FeignClient", "HttpExchange", "HttpExchangeInterface"}
FEIGN_ANNOTATION = "FeignClient"  # legacy single-name alias (tests/imports)
# Method-level REST annotations on a Feign client interface → the HTTP verb.
REST_VERB_BY_ANN = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH",
    # Spring 6 declarative HTTP interface annotations.
    "GetExchange": "GET", "PostExchange": "POST", "PutExchange": "PUT",
    "DeleteExchange": "DELETE", "PatchExchange": "PATCH",
}
# RestTemplate method → HTTP verb (exchange/execute take the verb as an arg).
HTTP_METHOD_BY_TEMPLATE_CALL = {
    "getForObject": "GET", "getForEntity": "GET",
    "postForObject": "POST", "postForEntity": "POST",
    "put": "PUT", "patchForObject": "PATCH", "delete": "DELETE",
}
# async-http-client: verb encoded in the prepare* method name.
HTTP_VERB_BY_ASYNC_METHOD = {
    "prepareGet": "GET", "preparePost": "POST", "preparePut": "PUT",
    "preparePatch": "PATCH", "prepareDelete": "DELETE",
    "prepareHead": "HEAD", "prepareOptions": "OPTIONS",
}
# Apache HttpComponents: verb from the request class (new HttpGet("…") → GET).
HTTP_VERB_BY_APACHE_REQUEST = {
    "HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT",
    "HttpDelete": "DELETE", "HttpPatch": "PATCH", "HttpHead": "HEAD",
    "HttpOptions": "OPTIONS", "HttpTrace": "TRACE",
}
# Field types treated as Apache HttpComponents clients. A plain HttpClient field
# calling execute is apache (java.net.http uses send/sendAsync).
APACHE_CLIENT_TYPES = {"CloseableHttpClient", "DefaultCloseableHttpClient", "HttpClient"}
ASYNC_CLIENT_TYPES = {"AsyncHttpClient", "DefaultAsyncHttpClient"}

# REST path keys, in preference order (handles @*Mapping(value=..., path=...)).
REST_PATH_KEYS = ("value", "path", "_raw")

# Fluent HTTP-client verb methods (WebClient / RestClient): the verb lives on a
# chained call separate from the .uri(url) call, so these are detected via the
# fluent-chain pass rather than the per-invocation field-type match.
FLUENT_VERB_METHODS = {
    "get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
    "delete": "DELETE", "head": "HEAD", "options": "OPTIONS",
}
# Field types whose fluent chains mark an outbound HTTP call. ``Builder`` is only
# accepted when the chain calls ``.build()`` first (WebClient.Builder pattern),
# which the fluent detector verifies — "Builder" alone is far too generic.
FLUENT_HTTP_FIELD_TYPES = {"WebClient", "RestClient", "Builder"}

# STOMP return-side producers (Spring messaging): the return value is brokered.
STOMP_PRODUCER_ANN = {"SendTo", "SendToUser"}

# ── In-house message-bus facades ────────────────────────────────────────────
# Many estates route all messaging through an in-house abstraction: an injected
# interface (``MessageBus``, ``EventBus``, …) whose send/publish methods take
# the *payload object* — the destination is resolved from the payload's type at
# runtime, not passed as a string. The payload type is therefore the join key
# linking producer to consumer. We can't enumerate every estate's facade by
# name, so the match is structural: a send/publish-style call whose first
# argument is a message-shaped object (a ``new Payload(...)`` literal, a
# ``Payload.builder()…build()`` chain, or an identifier whose declared type is
# a known message type), on a receiver whose declared field type looks like a
# bus. Bus-looking = the type name contains "bus"/"broker"/"gateway", or ends
# with one of these suffixes — conservative so ordinary service calls named
# ``send`` don't match.
BUS_FIELD_TYPE_SUFFIXES = ("bus", "broker", "gateway", "messageproducer", "producer")
BUS_FIELD_TYPE_CONTAINS = ("messagebus", "eventbus", "commandbus")
BUS_SEND_METHODS = {"send", "sendAndAwait", "publish", "dispatch", "emit", "fire"}
# Payload args that are clearly NOT message objects (byte[]/String transports,
# contexts, timing args of the bus API itself).
BUS_NON_PAYLOAD_TYPES = ("byte[]", "String", "MessageContext", "ZonedDateTime", "Instant", "Map")

# Consumer-side counterpart: class marker + method annotation on handler
# classes dispatched by payload type. In-house vocabulary — matched broadly
# but BOTH must be present, so a stray @Handle on a non-handler class is
# ignored, and a @MessageHandler class without annotated methods emits nothing.
BUS_HANDLER_CLASS_ANN = {"MessageHandler", "BusHandler", "EventHandler", "Consumer", "Listener"}
BUS_HANDLER_METHOD_ANN = {"Handle", "Handles", "OnMessage", "MessageHandler", "Consume", "Subscribe"}


def _looks_like_bus_type(type_name: str) -> bool:
    """Is this declared field type plausibly a message-bus facade interface?"""
    if not type_name:
        return False
    t = type_name.rsplit(".", 1)[-1].lower()
    if any(seg in t for seg in BUS_FIELD_TYPE_CONTAINS):
        return True
    return t.endswith(BUS_FIELD_TYPE_SUFFIXES)


def _walk_nodes(node) -> iter:
    """Depth-first walk of a tree-sitter node's descendants."""
    if node is None:
        return
    stack = list(node.children)
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(cur.children))


def _is_urlish(val: str) -> bool:
    return val.startswith("/") or val.startswith("http") or "${" in val or "#{" in val


def _sqs_queue_name(dest: str) -> str:
    """Bare queue name from an SQS destination (URL / ARN / bare name).

    ``https://sqs.eu-west-1.amazonaws.com/123456789012/orders`` and
    ``arn:aws:sqs:eu-west-1:123456789012:orders`` both → ``orders`` — the
    form ``@SqsListener`` uses, so both sides of the channel join exactly.
    """
    if not dest:
        return dest
    if dest.startswith("arn:"):
        return dest.rsplit(":", 1)[-1]
    if dest.startswith("http"):
        return dest.rstrip("/").rsplit("/", 1)[-1]
    return dest


# Destination-factory methods: JMSContext.createQueue("q") / createTopic("t")
# (JMS 2.0) and analogous helpers — the channel is the factory's string arg.
DESTINATION_FACTORY_METHODS = {"createQueue", "createTopic"}


class JvmProducerDetector:
    """Detects message producers and sync HTTP calls across an indexed codebase.

    Holds the JVM producer/HTTP-client tables and the per-invocation logic. The
    entry detector delegates body scanning and the Feign/STOMP special cases to
    an instance of this class.
    """

    def __init__(self, index: SymbolIndex):
        self.index = index
        self.java = java_ast

    # ── Feign / declarative HTTP clients ────────────────────────────

    def feign_calls(self, ci: TypeInfo, m_node, m_name, annotations) -> list[Producer]:
        """Outbound HTTP calls declared by a declarative HTTP-client interface.

        Covers @FeignClient (Spring Cloud OpenFeign) and @HttpExchange
        interfaces (Spring 6 declarative HTTP, proxied via WebClient). Channel
        = base url (from the class annotation's url attr, resolved) + method path.
        """
        out: list[Producer] = []
        base = ""
        for ann in self.java.get_class_annotations(ci.node):
            if self.java.get_annotation_name(ann) not in FEIGN_ANNOTATIONS:
                continue
            args = self.java.get_annotation_args(ann)
            raw_url = (args.get("url") or args.get("value") or [""])[0]
            if raw_url:
                base = self.index.resolve_channel(raw_url, ci)
            break
        for ann in annotations:
            ann_name = self.java.get_annotation_name(ann)
            if ann_name not in REST_VERB_BY_ANN:
                continue
            args = self.java.get_annotation_args(ann)
            paths: list[str] = []
            for k in REST_PATH_KEYS:
                if k in args:
                    paths = args[k]
                    break
            for p in (paths or ["unknown"]):
                path = self._join_rest_path(base, p) if base else p
                path = http_paths.strip_http_origin(path) or "/"
                out.append(Producer(
                    id=f"{ci.repo}:{ci.simple_name}.{m_name}:http:{p}",
                    repo=ci.repo,
                    type=ProducerType.HTTP_CALL,
                    channel=path,
                    method=f"{ci.simple_name}.{m_name}",
                    file=ci.file,
                    line=m_node.start_point[0] + 1,
                    message_type=REST_VERB_BY_ANN[ann_name],
                    # The interface method's return type — the HTTP analog of a
                    # message payload: "calls FulfillmentStatus through ...".
                    response_type=self.java.get_method_return_type(m_node) or "",
                ))
        return out

    # ── Sync HTTP calls (RestTemplate / WebClient / apache / async …) ──

    def http_calls_from_invocation(self, ci, m_name, inv, apache_map=None) -> list[Producer]:
        parsed = self.java.parse_method_invocation(inv)
        receiver = parsed["receiver"]
        method_called = parsed["method"]
        if not receiver:
            return []
        field_type = self.index.field_type(ci, receiver)
        if not field_type or field_type not in HTTP_CLIENT_TYPES:
            return []
        if method_called not in HTTP_CLIENT_TYPES[field_type]:
            return []

        is_apache = (
            field_type in APACHE_CLIENT_TYPES and method_called == "execute"
            and field_type != "HttpClient"
        ) or (field_type == "HttpClient" and method_called == "execute")
        if is_apache:
            path, verb = self._apache_request_info(inv, apache_map)
            if not path:
                return []
        elif field_type in ASYNC_CLIENT_TYPES:
            verb = HTTP_VERB_BY_ASYNC_METHOD.get(method_called, "")
            path = self._extract_http_path(inv, field_type)
            if not path:
                return []
        else:
            verb = HTTP_METHOD_BY_TEMPLATE_CALL.get(method_called, "")
            if method_called == "exchange":
                for a in parsed["args"]:
                    up = str(a).upper()
                    if up in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                        verb = up
                        break
            path = self._extract_http_path(inv, field_type)
            if not path:
                return []

        channel = self.index.resolve_channel(path, ci) or path
        channel = http_paths.strip_http_origin(channel)
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

    # ── Apache HttpComponents request extraction ───────────────────

    def apache_request_map(self, body: Node) -> dict:
        """``var_name → (url, verb)`` for every ``HttpXxx req = new HttpXxx("…")``
        declared in the method body. Built once per method so each
        ``execute(req)`` is an O(1) lookup instead of re-walking the body."""
        out: dict[str, tuple[str, str]] = {}
        for node in _walk_nodes(body):
            if node.type != "local_variable_declaration":
                continue
            for d in node.children:
                if d.type != "variable_declarator":
                    continue
                declared = ""
                for vc in d.children:
                    if vc.type == "identifier" and not declared:
                        declared = vc.text.decode()
                if not declared:
                    continue
                for vc in d.children:
                    if vc.type == "object_creation_expression":
                        url, verb = self._apache_request_from_creation(vc)
                        if url:
                            out[declared] = (url, verb)
                        break
        return out

    def _apache_request_info(self, invocation: Node, apache_map: dict) -> tuple[str, str]:
        arglist = next(
            (c for c in invocation.children if c.type == "argument_list"), None
        )
        if arglist is None:
            return "", ""
        for ac in arglist.children:
            if ac.type == "object_creation_expression":
                url, verb = self._apache_request_from_creation(ac)
                if url:
                    return url, verb
            elif ac.type == "identifier" and ac.text.decode() in apache_map:
                return apache_map[ac.text.decode()]
        return "", ""

    def _apache_request_from_creation(self, node: Node) -> tuple[str, str]:
        type_name = ""
        for c in node.children:
            if c.type in ("type_identifier", "scoped_identifier", "scoped_type_identifier"):
                type_name = self.java.get_type_name(c)
                break
        verb = HTTP_VERB_BY_APACHE_REQUEST.get(type_name, "")
        arglist = next((c for c in node.children if c.type == "argument_list"), None)
        if arglist is not None:
            for ac in arglist.children:
                if ac.type == "string_literal":
                    val = self.java.extract_string_value(ac)
                    if val and _is_urlish(val):
                        return val, verb
        return "", verb

    def _extract_http_path(self, invocation: Node, field_type: str) -> str:
        parsed = self.java.parse_method_invocation(invocation)
        for a in parsed["args"]:
            if isinstance(a, str) and _is_urlish(a):
                return a
        for child in _walk_nodes(invocation):
            if child.type != "method_invocation":
                continue
            inner = self.java.parse_method_invocation(child)
            if inner["method"] in ("uri", "url", "target", "path") and inner["args"]:
                a = inner["args"][0]
                if isinstance(a, str) and _is_urlish(a):
                    return a
        return ""

    # ── Fluent HTTP clients (WebClient / RestClient / WebClient.Builder) ──
    # These split verb and URL across chained calls
    # (``wc.get().uri(url)`` or ``builder.build().post().uri(url)``), which the
    # per-invocation match cannot see. We find ``.uri(url)``/``.url(url)`` calls
    # carrying an HTTP URL, then walk the chain inward to its root field and the
    # verb call. A verb must be present, and the root field type must be
    # WebClient / RestClient, or ``Builder`` reached via ``.build()``.

    def fluent_http_calls(self, ci: TypeInfo, m_name: str, body: Node) -> list[Producer]:
        out: list[Producer] = []
        for inv in self.java.find_method_invocations(body):
            parsed = self.java.parse_method_invocation(inv)
            if parsed["method"] not in ("uri", "url") or not parsed["args"]:
                continue
            url = parsed["args"][0]
            if not isinstance(url, str) or not _is_urlish(url):
                continue
            root, verb, saw_build = self._fluent_chain_root(inv)
            if not root or not verb:
                continue
            ftype = self.index.field_type(ci, root)
            is_http = (
                ftype in ("WebClient", "RestClient")
                or (ftype == "Builder" and saw_build)
            )
            if not is_http:
                continue
            channel = self.index.resolve_channel(url, ci) or url
            channel = http_paths.strip_http_origin(channel)
            if not channel or channel == "unknown":
                continue
            out.append(Producer(
                id=f"{ci.repo}:{ci.simple_name}.{m_name}:http:{channel}",
                repo=ci.repo,
                type=ProducerType.HTTP_CALL,
                channel=channel,
                method=f"{ci.simple_name}.{m_name}",
                file=ci.file,
                line=inv.start_point[0] + 1,
                message_type=verb,
            ))
        return out

    def _fluent_chain_root(self, inv: Node) -> tuple[str, str, bool]:
        """Walk a fluent chain inward to its root receiver.

        Returns ``(root_receiver_name, verb, saw_build)``. ``verb`` is the HTTP
        verb if a WebClient-style verb method appears in the chain; ``saw_build``
        is True if ``.build()`` appears (WebClient.Builder → WebClient).
        """
        verb = ""
        saw_build = False
        node = inv
        depth = 0
        while node is not None and depth < 25:
            depth += 1
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                m = name_node.text.decode() if name_node is not None else ""
                if m in FLUENT_VERB_METHODS:
                    verb = FLUENT_VERB_METHODS[m]
                elif m == "build":
                    saw_build = True
                node = node.child_by_field_name("object")
            elif node.type == "identifier":
                return node.text.decode(), verb, saw_build
            elif node.type == "field_access":
                return node.text.decode().rsplit(".", 1)[-1], verb, saw_build
            else:
                return "", verb, saw_build
        return "", verb, saw_build

    # ── Message producers (Kafka/Rabbit/JMS/Event/Pulsar/NATS/…) ────

    @staticmethod
    def grpc_stub_service(field_type: str) -> str:
        """gRPC service name from a generated stub field type, else ``""``.

        ``OrderServiceGrpc.OrderServiceBlockingStub`` → ``OrderService``. The
        raw text of a scoped_type_identifier field is kept verbatim by the
        index, so both scoped and simple forms reduce correctly.
        """
        t = (field_type or "").rsplit(".", 1)[-1]
        for suf in GRPC_STUB_SUFFIXES:
            if t.endswith(suf) and len(t) > len(suf):
                return t[: -len(suf)]
        return ""

    def producers_from_invocation(self, ci, m_name, inv, local_types: Optional[dict] = None) -> list[Producer]:
        parsed = self.java.parse_method_invocation(inv)
        receiver = parsed["receiver"]
        method_called = parsed["method"]
        args = parsed["args"]
        if not receiver:
            return []

        field_type = self.index.field_type(ci, receiver) or (local_types or {}).get(receiver, "")

        # Axon event-sourcing publisher: AggregateLifecycle.apply(new Event(…))
        # — the aggregate's event emission, routed by payload type like the
        # gateways. Only the qualified form; a bare apply(...) is too generic.
        if receiver == "AggregateLifecycle" and method_called == "apply":
            payload = self._bus_payload_type(inv, ci, {}, {})
            if payload:
                payload = self.index.resolve_fqn(ci, payload) or payload
                return [Producer(
                    id=f"{ci.repo}:{ci.simple_name}.{m_name}:apply:{payload}",
                    repo=ci.repo,
                    type=ProducerType.MESSAGE_BUS_PRODUCER,
                    channel=payload,
                    method=f"{ci.simple_name}.{m_name}",
                    file=ci.file,
                    line=inv.start_point[0] + 1,
                    message_type=payload,
                )]
            return []

        # gRPC client stub: any method call on a generated stub type. The
        # channel mirrors the server-side format (/Service/method) exactly, so
        # gRPC→gRPC edges link without a separate linker pass.
        svc = self.grpc_stub_service(field_type)
        if svc:
            return [Producer(
                id=f"{ci.repo}:{ci.simple_name}.{m_name}:grpc:{svc}/{method_called}",
                repo=ci.repo,
                type=ProducerType.GRPC_CALL,
                channel=f"/{svc}/{method_called}",
                method=f"{ci.simple_name}.{m_name}",
                file=ci.file,
                line=inv.start_point[0] + 1,
                message_type=field_type.rsplit(".", 1)[-1],
            )]

        # Payload-routed dispatch (Axon gateways/buses): the payload type is
        # the routing key, FQN-resolved like the in-house bus so sibling repos
        # with same-simple-named commands don't falsely link.
        if field_type in PAYLOAD_ROUTED_TYPES and method_called in PAYLOAD_ROUTED_TYPES[field_type]:
            payload = self._bus_payload_type(inv, ci, {}, {})
            if payload:
                payload = self.index.resolve_fqn(ci, payload) or payload
                return [Producer(
                    id=f"{ci.repo}:{ci.simple_name}.{m_name}:{method_called}:{receiver}:{payload}",
                    repo=ci.repo,
                    type=ProducerType.MESSAGE_BUS_PRODUCER,
                    channel=payload,
                    method=f"{ci.simple_name}.{m_name}",
                    file=ci.file,
                    line=inv.start_point[0] + 1,
                    message_type=payload,
                )]
            return []

        if not field_type or field_type not in PRODUCER_TYPES:
            return []
        if method_called not in PRODUCER_TYPES[field_type]:
            return []

        prod_type = PRODUCER_TYPE_LABEL.get(field_type, ProducerType.UNKNOWN)

        channel = args[0] if args else ""
        message_type = ""
        if field_type in EVENT_PUBLISHER_TYPES:
            channel = self._extract_event_type_from_args(inv) or (args[0] if args else "")
            message_type = channel
        else:
            message_type = self._extract_message_type_from_invocation(inv)

        # `kafkaTemplate.send(message)` — the arg is a local variable, and the
        # topic lives in the MessageBuilder chain that built it:
        #   MessageBuilder.withPayload(p).setHeader(TOPIC, "order-topic").build()
        # Resolve the variable to that chain and pull the topic string out.
        if (
            prod_type == ProducerType.KAFKA_PRODUCER
            and len(args) == 1
            and channel
            and channel[0].islower()
        ):
            topic = self._topic_from_message_builder(inv, channel)
            if topic:
                channel = topic

        resolved = self.index.resolve_channel(channel, ci) if channel else "unknown"
        # Destination passed as a factory call — ctx.createQueue("legacy-q") —
        # the channel is the factory's string literal.
        if resolved not in ("", "unknown"):
            arglist = next(
                (c for c in inv.children if c.type == "argument_list"), None
            )
            if arglist is not None:
                for ac in arglist.children:
                    if ac.type != "method_invocation":
                        continue
                    inner = self.java.parse_method_invocation(ac)
                    if inner["method"] in DESTINATION_FACTORY_METHODS and inner["args"]:
                        resolved = inner["args"][0]
                        break
        # SQS destinations arrive as queue URLs or ARNs; the consumer side
        # (@SqsListener) uses the bare queue name. Normalize to the name so
        # both sides of the channel join.
        if prod_type == ProducerType.SQS_PRODUCER:
            resolved = _sqs_queue_name(resolved)

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

    def bus_producer_from_invocation(self, ci, m_name, inv, local_types: dict, params: Optional[dict] = None) -> list[Producer]:
        """Producer through an in-house message-bus facade (``bus.send(payload)``).

        The facade carries no channel string — the destination is derived from
        the payload's type at runtime — so the *payload type* is the channel.
        Consumers keyed on the same type (``@Handle void m(Payload p)``) then
        link through it. The key is the payload's FQN when the type can be
        pinned down import-aware — sibling repos routinely declare
        same-simple-named messages in different packages, and a simple-name
        channel would falsely link them. Returns [] unless the receiver's
        field type looks like a bus and a payload type can be extracted.
        """
        parsed = self.java.parse_method_invocation(inv)
        receiver = parsed["receiver"]
        method_called = parsed["method"]
        if not receiver or method_called not in BUS_SEND_METHODS:
            return []

        field_type = self.index.field_type(ci, receiver)
        if not _looks_like_bus_type(field_type):
            return []
        # A known payload-routed dispatch API (Axon gateways/buses) matches the
        # bus-shape heuristic too — the specific detector owns those; don't
        # emit a duplicate here.
        if field_type in PAYLOAD_ROUTED_TYPES and method_called in PAYLOAD_ROUTED_TYPES.get(field_type, set()):
            return []

        payload = self._bus_payload_type(inv, ci, local_types, params)
        if not payload:
            return []
        payload = self.index.resolve_fqn(ci, payload) or payload

        return [Producer(
            id=f"{ci.repo}:{ci.simple_name}.{m_name}:{method_called}:{receiver}:{payload}",
            repo=ci.repo,
            type=ProducerType.MESSAGE_BUS_PRODUCER,
            channel=payload,  # message type IS the routing key on a type-routed bus
            method=f"{ci.simple_name}.{m_name}",
            file=ci.file,
            line=inv.start_point[0] + 1,
            message_type=payload,
        )]

    def _bus_payload_type(self, invocation: Node, ci, local_types: dict, params: Optional[dict] = None) -> str:
        """Payload type from a bus send/publish call's first argument.

        Priority: ``new Payload(...)`` object creation → ``Payload.builder()``
        chain root → identifier whose declared type (local var, method param,
        or field) is a message-shaped class. Skips transport overloads whose
        first arg is byte[]/String — those carry no type key. Returns the type
        as written (simple or scoped) — the caller resolves it to an FQN.
        """
        arglist = next(
            (c for c in invocation.children if c.type == "argument_list"), None
        )
        if arglist is None:
            return ""
        arg = next(
            (c for c in arglist.children if c.type not in ("(", ")", ",", "comment")),
            None,
        )
        if arg is None:
            return ""

        # 1) new Payload(...)
        if arg.type == "object_creation_expression":
            return self._payload_from_creation(arg)

        # 2) Payload.builder()....build() chain — walk to the root identifier
        if arg.type == "method_invocation":
            root, _verb, saw_build = self._fluent_chain_root(arg)
            if root and saw_build and root[0].isupper():
                return root

        # 3) plain identifier: local var, method param, or field of a
        #    message-shaped type
        if arg.type == "identifier":
            name = arg.text.decode()
            decl_type = (
                local_types.get(name)
                or (params or {}).get(name)
                or self.index.field_type(ci, name)
            )
            if not decl_type or decl_type in BUS_NON_PAYLOAD_TYPES:
                return ""
            decl_type = decl_type.rsplit(".", 1)[-1]
            # `var step = ...` (inferred) and lowercase primitives carry no
            # usable type key — an inferred-name channel would be noise.
            if not decl_type[:1].isupper():
                return ""
            # Generic type variables (``T extends Command``) and parameterized
            # wrappers (``SoapEnvelopeBase<T>``) are not concrete message types
            # — a type-routed bus keys on the concrete payload class, so these
            # carry no usable routing key.
            if len(decl_type) == 1 or "<" in decl_type:
                return ""
            return decl_type
        return ""

    def _payload_from_creation(self, node: Node) -> str:
        """Simple payload type from ``new za.co.x.Payload(...)`` — last segment."""
        for c in node.children:
            if c.type in ("type_identifier", "scoped_identifier", "scoped_type_identifier"):
                return self.java.last_seg(c)
        return ""

    def _topic_from_message_builder(self, invocation: Node, var_name: str) -> str:
        """Topic string from the MessageBuilder chain assigned to ``var_name``.

        Walks outward to the enclosing method body, finds
        ``<type> var_name = MessageBuilder...setHeader(<...TOPIC|...>, "topic")…build()``
        and returns the header's string literal. Empty when the variable
        isn't found or carries no topic header (e.g. `send(topic, payload)`
        overloads never reach here — they pass a url-ish first arg).
        """
        # 1) climb to the enclosing method body (invocation is nested inside it)
        body = invocation
        while body is not None and body.type != "block":
            body = body.parent
        if body is None:
            return ""

        # 2) find the local declaration whose declarator name matches var_name
        for decl in _walk_nodes(body):
            if decl.type != "local_variable_declaration":
                continue
            for vd in decl.children:
                if vd.type != "variable_declarator":
                    continue
                idents = [c for c in vd.children if c.type == "identifier"]
                if not idents or idents[0].text.decode() != var_name:
                    continue
                # 3) scan the initializer (or whole declarator) for setHeader
                #    with a string-literal second arg
                for inv2 in self.java.find_method_invocations(vd):
                    parsed = self.java.parse_method_invocation(inv2)
                    if parsed["method"] != "setHeader" or len(parsed["args"]) < 2:
                        continue
                    val = parsed["args"][1]
                    if isinstance(val, str) and val:
                        return val
        return ""

    def _extract_event_type_from_args(self, invocation: Node) -> str:
        """For publishEvent(new OrderCreatedEvent(...)), extract 'OrderCreatedEvent'."""
        for child in invocation.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "object_creation_expression":
                        for oc in arg.children:
                            if oc.type in ("type_identifier", "scoped_identifier"):
                                return self.java.last_seg(oc)
        return ""

    def _extract_message_type_from_invocation(self, invocation: Node) -> str:
        """Best-effort message type from invocation arguments.

        Priority: (1) the type of a ``new Payload(...)`` object creation — the
        canonical producer pattern, e.g. ``convertAndSend("order-events",
        new OrderEvent(...))`` → ``OrderEvent``; (2) the first uppercase-token
        arg (strip a ``Class.`` prefix if present).
        """
        # 1) object-creation payloads — Spring Kafka/Rabbit/JMS etc.
        for child in invocation.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "object_creation_expression":
                        for oc in arg.children:
                            if oc.type in ("type_identifier", "scoped_identifier"):
                                return self.java.last_seg(oc)
        # 2) fallback: uppercase-token identifier / constant args
        inv = self.java.parse_method_invocation(invocation)
        for arg in inv.get("args", []):
            if arg and arg[0].isupper():
                return arg.rsplit(".", 1)[-1]
        return ""

    # ── STOMP return-side producers (@SendTo / @SendToUser) ─────────

    def stomp_producer(self, ci, m_node, m_name, annotations) -> list[Producer]:
        out: list[Producer] = []
        ret = self.java.get_method_return_type(m_node) or ""
        for a in annotations:
            name = self.java.get_annotation_name(a)
            if name not in STOMP_PRODUCER_ANN:
                continue
            dests = self.java.get_annotation_args(a).get("_raw") or ["unknown"]
            for d in dests:
                resolved = self.index.resolve_channel(d, ci) or d
                out.append(Producer(
                    id=f"{ci.repo}:{ci.simple_name}.{m_name}:sendTo:{resolved}",
                    repo=ci.repo,
                    type=ProducerType.UNKNOWN,  # broker-agnostic STOMP destination
                    channel=resolved,
                    method=f"{ci.simple_name}.{m_name}",
                    file=ci.file,
                    line=m_node.start_point[0] + 1,
                    message_type=ret,
                ))
        return out

    # ── shared helpers ──────────────────────────────────────────────

    @staticmethod
    def _join_rest_path(prefix: str, channel: str) -> str:
        if channel == "unknown":
            return prefix
        if not channel.startswith("/"):
            return f"{prefix}/{channel}"
        return f"{prefix}{channel}"
