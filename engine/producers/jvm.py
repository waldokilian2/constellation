"""
JVM producer + sync-HTTP-call detection.

Tables and per-invocation logic moved out of the entry detector so producer
knowledge is isolated and swappable. Behaviour is unchanged from the previous
inlined implementation.
"""
from __future__ import annotations
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
}

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
}


# ── Sync HTTP client detection tables ─────────────────────────────────────

# Feign client interfaces: outbound HTTP calls, NOT REST entry points.
FEIGN_ANNOTATION = "FeignClient"
# Method-level REST annotations on a Feign client interface → the HTTP verb.
REST_VERB_BY_ANN = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH",
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


class JvmProducerDetector:
    """Detects message producers and sync HTTP calls across an indexed codebase.

    Holds the JVM producer/HTTP-client tables and the per-invocation logic. The
    entry detector delegates body scanning and the Feign/STOMP special cases to
    an instance of this class.
    """

    def __init__(self, index: SymbolIndex):
        self.index = index
        self.java = java_ast

    # ── Feign (declarative HTTP clients) ────────────────────────────

    def feign_calls(self, ci: TypeInfo, m_node, m_name, annotations) -> list[Producer]:
        """Outbound HTTP calls declared by a @FeignClient interface method.

        Channel = base url (from @FeignClient url attr, resolved) + method path.
        """
        out: list[Producer] = []
        base = ""
        for ann in self.java.get_class_annotations(ci.node):
            if self.java.get_annotation_name(ann) != FEIGN_ANNOTATION:
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
        node = inv.child_by_field_name("object")
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

    # ── Message producers (Kafka/Rabbit/JMS/Event/Pulsar/NATS) ──────

    def producers_from_invocation(self, ci, m_name, inv) -> list[Producer]:
        parsed = self.java.parse_method_invocation(inv)
        receiver = parsed["receiver"]
        method_called = parsed["method"]
        args = parsed["args"]
        if not receiver:
            return []

        field_type = self.index.field_type(ci, receiver)
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
