"""Tier 1 / Tier 2 detection regression checks.

Covers the framework support added on top of the original Spring/Java EE set:
main(), lifecycle hooks, Servlet API, SOAP, GraphQL, gRPC, Spring Cloud
Function, STOMP @SendTo, Apache Camel, Pulsar/NATS producers, Apache &
async HTTP clients, plus the parser fixes for nested-type supertypes and
chained method calls and the multi-level (hierarchy) call resolution.

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).
"""
from __future__ import annotations
import tempfile
from pathlib import Path

from engine.parser import JavaParser
from engine.java_index import JavaIndex
from engine.entry_detector import EntryPointDetector
from engine.cross_repo import CrossRepoLinker
from engine.models import EntryPointType, ProducerType


# ── helpers ───────────────────────────────────────────────────────

def detect(sources: dict, repo: str = "svc"):
    """Build the index from ``{filename: java}`` and run the detector.

    Returns ``(index, entries, producers)``.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = []
        for name, code in sources.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(code)
            files.append((repo, root, p))
        idx = JavaIndex()
        idx.build(files)
        entries, producers = EntryPointDetector(idx).scan()
        return idx, entries, producers


def types(entries) -> set[str]:
    return {e.type.value for e in entries}


def find(entries, ep_type, channel=None):
    out = [e for e in entries if e.type == ep_type]
    if channel is not None:
        out = [e for e in out if e.channel == channel]
    return out


# ── models ────────────────────────────────────────────────────────

def test_new_entry_types_exist():
    values = {t.value for t in EntryPointType}
    for v in ("servlet", "soap-service", "graphql", "grpc-service",
              "lifecycle", "main", "cloud-function"):
        assert v in values, f"{v} missing from EntryPointType"


def test_new_producer_types_exist():
    values = {t.value for t in ProducerType}
    for v in ("pulsar-producer", "nats-producer"):
        assert v in values, f"{v} missing from ProducerType"


# ── Tier 1: main / lifecycle / servlet / soap / graphql ───────────

def test_main_method_detected():
    src = "class App { public static void main(String[] args) {} }"
    _, entries, _ = detect({"App.java": src})
    assert find(entries, EntryPointType.MAIN, "main"), "main() not detected"
    # Non-static or wrong-arity methods named main are ignored.
    src2 = "class App { void main() {} }"
    _, e2, _ = detect({"App.java": src2})
    assert not find(e2, EntryPointType.MAIN), "non-static main() should be ignored"


def test_lifecycle_post_construct_and_runner():
    src = """
    @Component class Boot implements CommandLineRunner {
        @PostConstruct void init() {}
        public void run(String... a) {}
    }
    """
    _, entries, _ = detect({"Boot.java": src})
    lc = find(entries, EntryPointType.LIFECYCLE)
    chans = {e.channel for e in lc}
    assert "@PostConstruct:init" in chans
    assert "@CommandLineRunner:run" in chans


def test_lifecycle_initializing_bean():
    src = "class Prep implements org.springframework.beans.factory.InitializingBean { public void afterPropertiesSet() {} }"
    _, entries, _ = detect({"Prep.java": src})
    assert find(entries, EntryPointType.LIFECYCLE, "@InitializingBean:afterPropertiesSet")


def test_servlet_web_servlet_verbs():
    src = """
    @WebServlet(urlPatterns = "/api/widget") class W extends HttpServlet {
        protected void doGet(HttpServletRequest r, HttpServletResponse s) {}
        protected void doPost(HttpServletRequest r, HttpServletResponse s) {}
    }
    """
    _, entries, _ = detect({"W.java": src})
    sv = find(entries, EntryPointType.SERVLET, "/api/widget")
    assert {e.method_type for e in sv} == {"GET", "POST"}


def test_servlet_web_filter():
    src = """
    @WebFilter(urlPatterns = "/api/sec/*") class F implements Filter {
        public void doFilter(ServletRequest r, ServletResponse s, FilterChain c) {}
    }
    """
    _, entries, _ = detect({"F.java": src})
    f = find(entries, EntryPointType.SERVLET, "/api/sec/*")
    assert f and f[0].method_type == "FILTER"


def test_soap_web_service_operation_name():
    src = """
    @WebService class OrderSoap {
        @WebMethod(operationName = "createOrder")
        public OrderResponse create(OrderRequest req) { return null; }
    }
    """
    _, entries, _ = detect({"O.java": src})
    soap = find(entries, EntryPointType.SOAP_SERVICE)
    assert soap and soap[0].channel == "createOrder"
    assert soap[0].method_type == "SOAP"


def test_graphql_mappings():
    src = """
    @Controller class Q {
        @QueryMapping(name = "orders") public List<Order> orders(String id) { return null; }
        @MutationMapping public Order update(OrderInput in) { return null; }
    }
    """
    _, entries, _ = detect({"Q.java": src})
    gql = {e.channel: e for e in find(entries, EntryPointType.GRAPHQL)}
    assert "orders" in gql and gql["orders"].method_type == "Query"
    assert "update" in gql and gql["update"].method_type == "Mutation"


# ── Tier 2: gRPC, Spring Cloud Function, STOMP, Camel ─────────────

def test_grpc_implbase_overrides():
    src = """
    class GreeterImpl extends GreeterGrpc.GreeterImplBase {
        @Override public void sayHello(HelloRequest req, StreamObserver<HelloReply> resp) {}
    }
    """
    idx, entries, _ = detect({"G.java": src})
    # Nested-type supertype (Outer.Inner) must be captured.
    ci = next(iter(idx.by_fqn.values()))
    assert "GreeterImplBase" in ci.supertypes, "scoped_type_identifier supertype not captured"
    grpc = find(entries, EntryPointType.GRPC_SERVICE)
    assert grpc and grpc[0].channel == "/Greeter/sayHello"


def test_cloud_function_beans():
    src = """
    @Configuration class Functions {
        @Bean public Function<OrderIn, OrderOut> processOrder() { return i -> null; }
        @Bean public Consumer<OrderIn> consumeOrder() { return i -> {}; }
        @Bean public Supplier<OrderOut> supplyOrder() { return () -> null; }
    }
    """
    _, entries, _ = detect({"F.java": src})
    cf = {e.channel: e for e in find(entries, EntryPointType.CLOUD_FUNCTION)}
    assert set(cf) == {"processOrder", "consumeOrder", "supplyOrder"}
    # Input type captured for Function/Consumer, empty for Supplier.
    assert cf["processOrder"].message_type == "OrderIn"
    assert cf["consumeOrder"].message_type == "OrderIn"
    assert cf["supplyOrder"].message_type == ""


def test_stomp_send_to_producer():
    src = """
    @Controller class Chat {
        @MessageMapping("/chat.send") @SendTo("/topic/messages")
        public Message send(Message m) { return m; }
    }
    """
    _, _, producers = detect({"C.java": src})
    stomp = [p for p in producers if p.channel == "/topic/messages"]
    assert stomp, "@SendTo producer not emitted"
    # The method is both a WebSocket entry AND a STOMP producer.
    _, entries, _ = detect({"C.java": src})
    assert find(entries, EntryPointType.WEBSOCKET, "/chat.send")


def test_camel_from_to_routing_and_types():
    src = """
    import org.apache.camel.builder.RouteBuilder;
    public class R extends RouteBuilder {
        public void configure() {
            from("kafka:orders?groupId=g").to("jms:queue:ship");
        }
    }
    """
    _, entries, producers = detect({"R.java": src})
    # from("kafka:orders") → kafka consumer on channel "orders" (query stripped).
    assert find(entries, EntryPointType.KAFKA_CONSUMER, "orders")
    # to("jms:queue:ship") → jms producer on channel "ship".
    ship = [p for p in producers if p.channel == "ship"]
    assert ship and ship[0].type == ProducerType.JMS_PRODUCER


def test_camel_unknown_scheme_dropped():
    src = """
    import org.apache.camel.builder.RouteBuilder;
    public class R extends RouteBuilder {
        public void configure() { from("direct:start").to("log:out"); }
    }
    """
    _, entries, producers = detect({"R.java": src})
    # direct:/log: are not mapped broker schemes → no entries/producers.
    assert not any(e.type == EntryPointType.KAFKA_CONSUMER for e in entries)
    assert not producers


def test_camel_links_to_kafka_publisher():
    # Cross-repo: a KafkaTemplate.send("orders") producer in one repo links to
    # a Camel from("kafka:orders") consumer in another.
    camel = "import org.apache.camel.builder.RouteBuilder; public class R extends RouteBuilder { public void configure() { from(\"kafka:orders\").to(\"mock:end\"); } }"
    pub = "public class P { org.springframework.kafka.core.KafkaTemplate k; public void go() { k.send(\"orders\", x); } }"
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "c").mkdir(); (d / "c" / "R.java").write_text(camel)
        (d / "p").mkdir(); (d / "p" / "P.java").write_text(pub)
        idx = JavaIndex()
        idx.build([("c", d / "c", d / "c" / "R.java"), ("p", d / "p", d / "p" / "P.java")])
        entries, producers = EntryPointDetector(idx).scan()
    links = CrossRepoLinker().link(entries, producers)
    orders = [l for l in links if l.channel == "orders"]
    assert orders and orders[0].kind == "message"
    assert any(i.startswith("p:") for i in orders[0].producers)
    assert any(i.startswith("c:") for i in orders[0].consumers)


# ── New producers: Pulsar / NATS ──────────────────────────────────

def test_pulsar_and_nats_producers():
    src = """
    public class Emit {
        PulsarTemplate<OrderEvent> pulsar;
        Connection nats;
        public void go() {
            pulsar.send("orders-events", evt);
            nats.publish("orders.created", body);
        }
    }
    """
    _, _, producers = detect({"E.java": src})
    by_chan = {p.channel: p for p in producers}
    assert "orders-events" in by_chan and by_chan["orders-events"].type == ProducerType.PULSAR_PRODUCER
    assert "orders.created" in by_chan and by_chan["orders.created"].type == ProducerType.NATS_PRODUCER


def test_rabbit_convert_send_and_receive_is_producer():
    src = """
    public class Rpc {
        RabbitTemplate rabbit;
        public void call() { Object r = rabbit.convertSendAndReceive("orders-rpc", payload); }
    }
    """
    _, _, producers = detect({"R.java": src})
    assert any(p.channel == "orders-rpc" for p in producers)


# ── New HTTP clients: Apache HttpComponents & async-http-client ───

def test_apache_httpclient_inline_request():
    src = """
    public class C {
        CloseableHttpClient apache;
        public void go() { apache.execute(new HttpGet("https://api.x/orders/123")); }
    }
    """
    _, _, producers = detect({"C.java": src})
    h = [p for p in producers if p.type == ProducerType.HTTP_CALL]
    assert h and h[0].channel == "/orders/123" and h[0].message_type == "GET"


def test_apache_httpclient_request_variable():
    src = """
    public class C {
        CloseableHttpClient apache;
        public void go() {
            HttpPost req = new HttpPost("https://api.x/orders");
            apache.execute(req);
        }
    }
    """
    _, _, producers = detect({"C.java": src})
    h = [p for p in producers if p.type == ProducerType.HTTP_CALL]
    assert h and h[0].channel == "/orders" and h[0].message_type == "POST"


def test_async_http_client_verb_from_method_name():
    src = """
    public class C {
        AsyncHttpClient async;
        public void go() { async.prepareDelete("https://api.x/orders/9").execute(); }
    }
    """
    _, _, producers = detect({"C.java": src})
    h = [p for p in producers if p.type == ProducerType.HTTP_CALL]
    assert h and h[0].channel == "/orders/9" and h[0].message_type == "DELETE"


# ── Parser fixes + hierarchy resolution ───────────────────────────

def test_chained_call_parse():
    p = JavaParser()
    root = p.parse_source(b'class X { void m() { from("kafka:orders").to("kafka:ship"); } }')
    invs = p.find_method_invocations(p.find_methods(p.find_classes(root)[0])[0])
    parsed = {p.parse_method_invocation(i)["method"] for i in invs}
    assert parsed == {"from", "to"}, f"chained from().to() mis-parsed: {parsed}"


def test_multi_level_hierarchy_resolution():
    # step() is defined on Grand, two levels above the receiver's type Child.
    src = """
    class Grand { void step() {} }
    class Parent extends Grand {}
    class Child extends Parent {}
    class Use {
        Child c;
        void go() { c.step(); }
    }
    """
    idx, entries, _ = detect({"U.java": src})
    # Resolve the call c.step() — should find Grand.step via the hierarchy fallback.
    use = next(c for c in idx.by_fqn.values() if c.simple_name == "Use")
    method, ambiguous, _ = idx.resolve_call(use, "c", "step", arity=0)
    assert method is not None, "multi-level hierarchy resolution failed"
    assert method.class_simple == "Grand"
    assert not ambiguous


def test_ejb_timeout_is_scheduled():
    src = """
    @Singleton class Timer {
        @Timeout public void handle(javax.ejb.Timer t) {}
    }
    """
    _, entries, _ = detect({"T.java": src})
    assert find(entries, EntryPointType.SCHEDULED_TASK)


# ── Post-review hardening regressions ─────────────────────────────

def _detect_multi(repos: dict, repo_files: dict):
    """Build a multi-repo index. ``repos`` = {repo: dir}, ``repo_files`` =
    {repo: {filename: java}}. Returns (index, entries, producers)."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        files = []
        for repo, mapping in repo_files.items():
            (d / repo).mkdir()
            for name, code in mapping.items():
                p = d / repo / name
                p.write_text(code)
                files.append((repo, d / repo, p))
        idx = JavaIndex()
        idx.build(files)
        entries, producers = EntryPointDetector(idx).scan()
        return idx, entries, producers


def test_grpc_requires_stream_observer_param():
    # A non-gRPC class extending a hand-written *ImplBase must NOT produce
    # gRPC entries — its @Override methods have no StreamObserver parameter.
    src = """
    class Handler extends AuditServiceImplBase {
        @Override public void process(Order e) {}
        @Override public void cleanup(String s) {}
    }
    """
    _, entries, _ = detect({"H.java": src})
    assert not find(entries, EntryPointType.GRPC_SERVICE), "non-gRPC *ImplBase produced gRPC entries"


def test_main_requires_string_array_and_void():
    # Wrong signature: non-void return / non String[] param → not a MAIN entry.
    src = "class Util { static String main(String x) { return x; } static void go(int n) {} }"
    _, entries, _ = detect({"U.java": src})
    assert not find(entries, EntryPointType.MAIN), "non-void/non-String[] main() must not be MAIN"


def test_camel_enrich_is_not_a_producer():
    # enrich() consumes from its endpoint (request-reply/poll); it must not be
    # recorded as a producer edge (which would invert link direction).
    src = """
    import org.apache.camel.builder.RouteBuilder;
    public class R extends RouteBuilder {
        public void configure() { from("jms:queue:in").enrich("kafka:orders"); }
    }
    """
    _, _, producers = detect({"R.java": src})
    assert not [p for p in producers if p.type == ProducerType.KAFKA_PRODUCER and p.channel == "orders"]


def test_apache_httpclient_qualified_request_keeps_verb():
    # A fully-qualified request class (scoped_type_identifier) must still yield
    # its HTTP verb — not an empty message_type.
    src = """
    public class C {
        org.apache.http.impl.client.CloseableHttpClient apache;
        public void go() { apache.execute(new org.apache.http.client.methods.HttpGet("https://api.x/o/1")); }
    }
    """
    _, _, producers = detect({"C.java": src})
    h = [p for p in producers if p.type == ProducerType.HTTP_CALL]
    assert h and h[0].message_type == "GET", "qualified apache request class lost its verb"


def test_graphql_channel_does_not_link_to_broker_topic():
    # A GraphQL operation and a Kafka topic sharing the bare name "orders" must
    # NOT produce a cross-repo message edge (non-broker channels are excluded).
    gql = "@Controller class G { @QueryMapping(name=\"orders\") public Object orders(String id) { return null; } }"
    pub = "public class P { org.springframework.kafka.core.KafkaTemplate k; public void go() { k.send(\"orders\", x); } }"
    _, entries, producers = _detect_multi(
        {"gql": {"G.java": gql}, "pub": {"P.java": pub}},
        {"gql": {"G.java": gql}, "pub": {"P.java": pub}},
    )
    links = CrossRepoLinker().link(entries, producers)
    assert not [l for l in links if l.channel == "orders"], "GraphQL op collided with a Kafka topic in linking"


def test_hierarchy_resolution_is_cached():
    src = """
    class Grand { void step() {} }
    class Parent extends Grand {}
    class Child extends Parent {}
    class Use { Child c; void go() { c.step(); } }
    """
    idx, _, _ = detect({"U.java": src})
    use = next(c for c in idx.by_fqn.values() if c.simple_name == "Use")
    m1, _, _ = idx.resolve_call(use, "c", "step", arity=0)
    # The cache is populated with the resolved method after the first lookup.
    assert ("Child", "step") in idx._hierarchy_cache
    cached = idx._hierarchy_cache[("Child", "step")]
    assert cached and cached[0].class_simple == "Grand"
    # Second call returns the cached result (same object).
    m2, _, _ = idx.resolve_call(use, "c", "step", arity=0)
    assert m2 is m1
