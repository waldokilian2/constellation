"""Java coverage Tier 1 detections (issue #116).

Pins the framework/communication coverage added on top of the message-bus
work — each group maps to a Tier 1 item from the issue:

  * @PulsarListener consumer (closes the PulsarTemplate asymmetry)
  * RocketMQTemplate producer + class-level @RocketMQMessageListener consumer
  * Spring Cloud AWS 3.x: SqsTemplate/SqsClient/AmazonSQS/SnsTemplate producers
    with URL/ARN → queue-name normalization, @SqsListener(queueNames=...) consumer
  * gRPC outbound client stubs → /Service/method channels that join the
    server-side GRPC_SERVICE entries (new grpc link kind)
  * Quarkus / SmallRye @Incoming/@Outgoing with mp.messaging.* config mapping
  * Micronaut companion-annotation listeners (@KafkaListener + @Topic, ...)
  * Spring 6 @HttpExchange declarative HTTP interfaces (Feign shape)
  * JMS 2.0 simplified API (JMSContext/JMSProducer + createQueue destinations)
  * MQTT (Paho) producers, Redis pub/sub, Axon payload-routed gateways

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from engine.symbol_index import SymbolIndex
from engine.entry_detector import EntryPointDetector
from engine.cross_repo import CrossRepoLinker
from engine.models import EntryPointType, ProducerType

from test_tier12_detection import detect, find


def detect_multi(files_by_repo: dict, config_by_repo: dict | None = None):
    """detect() across MULTIPLE repos: ``{repo: {file: java}}`` (+ optional
    ``{repo: application.properties text}``). Returns (index, entries,
    producers, links)."""
    config_by_repo = config_by_repo or {}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        triples = []
        for repo, sources in files_by_repo.items():
            rdir = root / repo
            for name, code in sources.items():
                p = rdir / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(code)
                triples.append((repo, rdir, p))
            if repo in config_by_repo:
                (rdir / "application.properties").write_text(config_by_repo[repo])
        idx = SymbolIndex()
        idx.build(triples)
        entries, producers = EntryPointDetector(idx).scan()
        links = CrossRepoLinker().link(entries, producers)
        return idx, entries, producers, links


# ── models ─────────────────────────────────────────────────────────

def test_new_type_values_exist():
    values = {t.value for t in EntryPointType}
    for v in ("pulsar-consumer", "mqtt-consumer", "reactive-incoming"):
        assert v in values, f"{v} missing from EntryPointType"
    pvalues = {t.value for t in ProducerType}
    for v in ("sqs-producer", "sns-producer", "grpc-call"):
        assert v in pvalues, f"{v} missing from ProducerType"


# ── Pulsar consumer ────────────────────────────────────────────────

def test_pulsar_listener_detected():
    src = """
    class PulsarConsumer {
        @PulsarListener(topics = "orders-pulsar")
        public void onOrder(OrderEvent e) {}
    }
    class OrderEvent {}
    """
    _, entries, _ = detect({"P.java": src})
    hits = [e for e in entries if e.type == EntryPointType.PULSAR_CONSUMER]
    assert hits, "@PulsarListener(topics=...) not detected"
    assert hits[0].channel == "orders-pulsar"


def test_pulsar_producer_consumer_link():
    """PulsarTemplate producer in one repo joins @PulsarListener in another."""
    _, _, _, links = detect_multi({
        "pub": {"P.java": """
            class PulsarPub {
                private PulsarTemplate<String> pulsar;
                void emit() { pulsar.send("orders-pulsar", "e"); }
            }
        """},
        "cons": {"C.java": """
            class PulsarCons {
                @PulsarListener(topics = "orders-pulsar")
                void onOrder(String e) {}
            }
        """},
    })
    assert any(l.channel == "orders-pulsar" and l.kind == "message" for l in links), \
        "Pulsar producer→consumer link missing"


# ── RocketMQ ───────────────────────────────────────────────────────

def test_rocketmq_template_producer():
    src = """
    class OrderProducer {
        private RocketMQTemplate rocket;
        void publish() { rocket.syncSend("order-topic", new OrderEvent()); }
    }
    class OrderEvent {}
    """
    _, _, producers = detect({"R.java": src})
    hits = [p for p in producers if p.channel == "order-topic"]
    assert hits, "RocketMQTemplate.syncSend not detected"
    assert hits[0].type == ProducerType.KAFKA_PRODUCER  # bucketed as Kafka-style


def test_rocketmq_class_level_listener():
    src = """
    @RocketMQMessageListener(topic = "order-topic-rocket", consumerGroup = "g")
    class RocketConsumer {
        public void onMessage(OrderEvent e) {}
    }
    class OrderEvent {}
    """
    _, entries, _ = detect({"C.java": src})
    hits = [e for e in entries if e.channel == "order-topic-rocket"]
    assert hits, "class-level @RocketMQMessageListener not detected"
    assert hits[0].method == "onMessage"


def test_rocketmq_producer_consumer_link():
    _, _, _, links = detect_multi({
        "pub": {"R.java": """
            class OrderProducer {
                private RocketMQTemplate rocket;
                void publish() { rocket.syncSend("order-topic-rocket", new OrderEvent()); }
            }
            class OrderEvent {}
        """},
        "cons": {"C.java": """
            @RocketMQMessageListener(topic = "order-topic-rocket", consumerGroup = "g")
            class RocketConsumer {
                public void onMessage(OrderEvent e) {}
            }
        """},
    })
    assert any(l.channel == "order-topic-rocket" for l in links), \
        "RocketMQ producer→consumer link missing"


# ── AWS SQS / SNS ──────────────────────────────────────────────────

def test_sqs_listener_queue_names_form():
    """Spring Cloud AWS 3.x named form — previously missed (only _raw worked)."""
    src = """
    class SqsConsumer {
        @SqsListener(queueNames = "orders")
        void onMsg(Msg m) {}
    }
    class Msg {}
    """
    _, entries, _ = detect({"C.java": src})
    hits = find(entries, EntryPointType.SQS_CONSUMER)
    assert hits, "@SqsListener(queueNames=...) not detected"
    assert hits[0].channel == "orders"


def test_sqs_producer_url_normalized_to_queue_name():
    """SqsTemplate.send(queueUrl, msg) → channel is the bare queue name, the
    form @SqsListener uses, so both sides join."""
    src = """
    class SqsSender {
        private SqsTemplate sqs;
        void send() { sqs.send("https://sqs.eu-west-1.amazonaws.com/123456789012/orders", new Msg()); }
    }
    class Msg {}
    """
    _, _, producers = detect({"S.java": src})
    hits = [p for p in producers if p.type == ProducerType.SQS_PRODUCER]
    assert hits, "SqsTemplate.send not detected"
    assert hits[0].channel == "orders", f"queue URL not normalized: {hits[0].channel}"


def test_sqs_producer_arn_normalized():
    src = """
    class SqsSender {
        private SqsClient sqs;
        void send() { sqs.sendMessage("arn:aws:sqs:eu-west-1:123456789012:orders", "body"); }
    }
    """
    _, _, producers = detect({"S.java": src})
    hits = [p for p in producers if p.type == ProducerType.SQS_PRODUCER]
    assert hits and hits[0].channel == "orders", "ARN form not normalized"


def test_sqs_producer_consumer_link():
    _, _, _, links = detect_multi({
        "pub": {"S.java": """
            class SqsSender {
                private SqsTemplate sqs;
                void send() { sqs.send("https://sqs.eu-west-1.amazonaws.com/123/orders", new Msg()); }
            }
            class Msg {}
        """},
        "cons": {"C.java": """
            class SqsConsumer {
                @SqsListener(queueNames = "orders")
                void onMsg(Msg m) {}
            }
        """},
    })
    assert any(l.channel == "orders" and l.kind == "message" for l in links), \
        "SQS URL→name normalization broke the producer→consumer link"


def test_sns_producer_detected():
    src = """
    class SnsNotifier {
        private SnsTemplate sns;
        void notify() { sns.send("arn:aws:sns:eu-west-1:123:order-events", new Msg()); }
    }
    class Msg {}
    """
    _, _, producers = detect({"N.java": src})
    hits = [p for p in producers if p.type == ProducerType.SNS_PRODUCER]
    assert hits, "SnsTemplate.send not detected"


# ── gRPC client stubs ──────────────────────────────────────────────

def test_grpc_stub_call_channel_format():
    """A generated *Stub field's method call → /Service/method — the exact
    format the server-side detector emits for *ImplBase overrides."""
    src = """
    class OrderClient {
        private OrderServiceGrpc.OrderServiceBlockingStub stub;
        void fetch() { stub.getOrder(GetOrderRequest.getDefaultInstance()); }
    }
    class OrderServiceGrpc { static class OrderServiceBlockingStub {} }
    class GetOrderRequest { static Object getDefaultInstance() { return null; } }
    """
    _, _, producers = detect({"Cl.java": src})
    hits = [p for p in producers if p.type == ProducerType.GRPC_CALL]
    assert hits, "gRPC *Stub call not detected"
    assert hits[0].channel == "/OrderService/getOrder"


def test_grpc_client_server_link():
    """grpc-call producer in one repo joins grpc-service entry in another via
    the shared /Service/method channel (new 'grpc' link kind)."""
    _, _, _, links = detect_multi({
        "order": {"Cl.java": """
            class OrderClient {
                private OrderServiceGrpc.OrderServiceBlockingStub stub;
                void fetch() { stub.getOrder(GetOrderRequest.getDefaultInstance()); }
            }
            class OrderServiceGrpc { static class OrderServiceBlockingStub {} }
            class GetOrderRequest { static Object getDefaultInstance() { return null; } }
        """},
        "inventory": {"S.java": """
            class OrderServiceImpl extends OrderServiceGrpc.OrderServiceImplBase {
                @Override public void getOrder(GetOrderRequest req, StreamObserver<GetOrderReply> resp) {}
            }
            class OrderServiceGrpc { static class OrderServiceImplBase {} }
            class GetOrderRequest {}
            class GetOrderReply {}
            class StreamObserver<T> {}
        """},
    })
    grpc_links = [l for l in links if l.kind == "grpc"]
    assert grpc_links and grpc_links[0].channel == "/OrderService/getOrder", \
        "gRPC client→server link missing"


def test_grpc_stub_variants_reduce():
    """*BlockingStub / *Stub / *FutureStub all reduce to the service name."""
    from engine.producers.jvm import JvmProducerDetector
    assert JvmProducerDetector.grpc_stub_service("OrderServiceGrpc.OrderServiceBlockingStub") == "OrderService"
    assert JvmProducerDetector.grpc_stub_service("OrderServiceGrpc.OrderServiceStub") == "OrderService"
    assert JvmProducerDetector.grpc_stub_service("OrderServiceGrpc.OrderServiceFutureStub") == "OrderService"
    assert JvmProducerDetector.grpc_stub_service("SomeService") == ""  # not a stub


# ── Quarkus / SmallRye Reactive Messaging ──────────────────────────

def test_reactive_incoming_detected():
    src = 'class MovieConsumer {\n    @Incoming("movies-in")\n    public void process(String title) {}\n}\n'
    _, entries, _ = detect({"Q.java": src})
    hits = [e for e in entries if e.type == EntryPointType.REACTIVE_INCOMING]
    assert hits, "@Incoming not detected"
    assert hits[0].channel == "movies-in"


def test_reactive_channel_resolved_from_config():
    """mp.messaging.incoming.<channel>.topic maps the logical channel to the
    physical Kafka topic."""
    src = 'class MovieConsumer {\n    @Incoming("movies-in")\n    public void process(String title) {}\n}\n'
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Q.java").write_text(src)
        (root / "application.properties").write_text(
            "mp.messaging.incoming.movies-in.topic=movies-topic\n"
        )
        idx = SymbolIndex()
        idx.build([("svc", root, root / "Q.java")])
        entries, _ = EntryPointDetector(idx).scan()
    hits = [e for e in entries if e.type == EntryPointType.REACTIVE_INCOMING]
    assert hits and hits[0].channel == "movies-topic", \
        "mp.messaging topic mapping not applied"


def test_reactive_outgoing_producer():
    src = 'class MovieProcessor {\n    @Outgoing("movies-out")\n    public String process(String title) { return title; }\n}\n'
    _, _, producers = detect({"Q.java": src})
    hits = [p for p in producers if p.channel == "movies-out"]
    assert hits, "@Outgoing producer not detected"


def test_reactive_outgoing_links_kafka_listener():
    """@Outgoing mapped to a physical topic joins a @KafkaListener elsewhere."""
    _, _, _, links = detect_multi(
        {
            "quarkus": {"Q.java": 'class MovieProcessor {\n    @Outgoing("movies-out")\n    public String process(String t) { return t; }\n}\n'},
            "spring": {"K.java": 'class MovieKafkaListener {\n    @KafkaListener(topics = "movies-processed")\n    void onMovie(String m) {}\n}\n'},
        },
        config_by_repo={"quarkus": "mp.messaging.outgoing.movies-out.topic=movies-processed\n"},
    )
    assert any(l.channel == "movies-processed" for l in links), \
        "@Outgoing→topic mapping did not produce a cross-repo link"


# ── Micronaut ──────────────────────────────────────────────────────

def test_micronaut_kafka_listener_companion_topic():
    """Micronaut @KafkaListener + @Topic: channel in the companion method
    annotation, not the listener attribute. Must not double-report."""
    src = """
    @KafkaListener
    class ProductListener {
        @Topic("products")
        void receive(Product p) {}
    }
    class Product {}
    """
    _, entries, _ = detect({"M.java": src})
    hits = find(entries, EntryPointType.KAFKA_CONSUMER)
    assert hits, "Micronaut @KafkaListener+@Topic not detected"
    assert hits[0].channel == "products"
    assert len(hits) == 1, f"expected one entry, got {len(hits)} (duplicate?)"


def test_micronaut_rabbit_listener_companion_queue():
    src = """
    @RabbitListener
    class OrderListener {
        @Queue("orders")
        void receive(Order o) {}
    }
    class Order {}
    """
    _, entries, _ = detect({"M.java": src})
    hits = find(entries, EntryPointType.RABBITMQ_CONSUMER)
    assert hits and hits[0].channel == "orders", "Micronaut @RabbitListener+@Queue not detected"


def test_spring_listener_still_uses_attribute_form():
    """Regression guard: Spring's @KafkaListener(topics=...) is unchanged."""
    src = 'class K {\n    @KafkaListener(topics = "orders")\n    void onOrder(String o) {}\n}\n'
    _, entries, _ = detect({"K.java": src})
    hits = find(entries, EntryPointType.KAFKA_CONSUMER)
    assert hits and hits[0].channel == "orders"


# ── Spring 6 @HttpExchange ─────────────────────────────────────────

def test_http_exchange_interface_is_outbound_http():
    src = """
    @HttpExchange(url = "/api/orders")
    interface OrderClientApi {
        @GetExchange("/status")
        OrderStatus getStatus(String id);
    }
    class OrderStatus {}
    """
    _, entries, producers = detect({"H.java": src})
    assert not find(entries, EntryPointType.REST_ENDPOINT), \
        "@HttpExchange interface must NOT be a server-side REST entry"
    hits = [p for p in producers if p.type == ProducerType.HTTP_CALL]
    assert hits, "@HttpExchange method not detected as outbound HTTP"
    assert hits[0].channel == "/api/orders/status"
    assert hits[0].message_type == "GET"


# ── JMS 2.0 simplified API ─────────────────────────────────────────

def test_jms2_producer_with_create_queue():
    src = """
    class LegacyJms {
        void send(JMSContext ctx) {
            JMSProducer producer = ctx.createProducer();
            producer.send(ctx.createQueue("legacy-orders"), "payload");
        }
    }
    class JMSContext { JMSProducer createProducer() { return null; } Object createQueue(String s) { return null; } }
    class JMSProducer { void send(Object dest, Object payload) {} }
    """
    _, _, producers = detect({"J.java": src})
    hits = [p for p in producers if p.type == ProducerType.JMS_PRODUCER]
    assert hits, "JMS 2.0 JMSProducer.send not detected"
    assert hits[0].channel == "legacy-orders", \
        f"createQueue destination not extracted: {hits[0].channel}"


# ── MQTT / Redis / Axon ────────────────────────────────────────────

def test_mqtt_paho_producer():
    src = """
    class TelemetryPub {
        private MqttClient client;
        void pub() { client.publish("devices/telemetry", new byte[0]); }
    }
    """
    _, _, producers = detect({"T.java": src})
    hits = [p for p in producers if p.channel == "devices/telemetry"]
    assert hits, "MqttClient.publish not detected"


def test_redis_pubsub_producer():
    src = """
    class RedisPub {
        private StringRedisTemplate redis;
        void pub() { redis.convertAndSend("cache-invalidate", "k"); }
    }
    """
    _, _, producers = detect({"R.java": src})
    hits = [p for p in producers if p.channel == "cache-invalidate"]
    assert hits, "StringRedisTemplate.convertAndSend not detected"


def test_axon_gateway_payload_routed():
    """CommandGateway.send(payload) routes by payload type — channel is the
    payload FQN, exactly like the in-house bus, and never duplicated."""
    src = """
    package app;
    import axon.cmd.RegisterCustomer;
    class CustomerService {
        private CommandGateway gateway;
        void register() { gateway.send(new RegisterCustomer()); }
    }
    class CommandGateway { void send(Object o) {} }
    """
    _, _, producers = detect({"A.java": src})
    hits = [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER
            and p.channel.endswith("RegisterCustomer")]
    assert hits, "CommandGateway.send not detected as payload-routed"
    assert len(hits) == 1, f"duplicate bus producers: {len(hits)}"


# ── Quarkus @Channel + Emitter producer ────────────────────────────

def test_quarkus_channel_emitter_producer():
    """@Channel("orders") Emitter<String> e; e.send(p) — the standard Quarkus/
    MicroProfile producer shape. Channel resolves via outgoing config when
    present (same key a @Incoming consumer resolves)."""
    src = """
    class QuotesResource {
        @Channel("quote-requests")
        Emitter<String> quoteRequestEmitter;
        void create() { quoteRequestEmitter.send("x"); }
    }
    """
    _, _, producers = detect({"Q.java": src})
    hits = [p for p in producers if p.channel == "quote-requests"]
    assert hits, "@Channel Emitter.send not detected as a producer"


def test_quarkus_channel_emitter_links_incoming_consumer():
    """Emitter producer (config-mapped topic) joins a @Incoming consumer on
    the same mapped physical channel."""
    _, _, _, links = detect_multi(
        {
            "producer": {"P.java": """
                class QuotesResource {
                    @Channel("requests")
                    Emitter<String> emitter;
                    void create() { emitter.send("x"); }
                }
            """},
            "processor": {"C.java": 'class Q {\n    @Incoming("req")\n    void process(String t) {}\n}\n'},
        },
        config_by_repo={
            "producer": "mp.messaging.outgoing.requests.topic=quote-requests\n",
            "processor": "mp.messaging.incoming.req.topic=quote-requests\n",
        },
    )
    assert any(l.channel == "quote-requests" for l in links), \
        "Emitter producer → @Incoming consumer link missing"


def test_plain_send_on_non_channel_field_ignored():
    """A send() on a field WITHOUT @Channel must not match (too generic)."""
    src = """
    class Svc {
        Emitter<String> emitter;
        void go() { emitter.send("x"); }
    }
    class Emitter<T> { void send(String s) {} }
    """
    _, _, producers = detect({"S.java": src})
    assert not [p for p in producers if p.channel == "x"], \
        "unannotated Emitter.send must not produce a channel"


# ── Axon consumers + aggregate event publishing ────────────────────

def test_axon_command_and_event_handlers():
    """@CommandHandler/@EventHandler/@QueryHandler methods consume by payload
    type (FQN channel), joining the gateway/apply producers."""
    src = """
    package app;
    import commons.cmd.AddCustomerCommand;
    import commons.event.CustomerAddedEvent;
    class CustomerAggregate {
        @CommandHandler
        public CustomerAggregate(AddCustomerCommand cmd) {}
        @EventSourcingHandler
        void on(CustomerAddedEvent e) {}
    }
    class Projector {
        @EventHandler
        void handle(CustomerAddedEvent e) {}
    }
    """
    _, entries, _ = detect({"A.java": src})
    cmds = [e for e in entries if e.channel.endswith("AddCustomerCommand")]
    evts = [e for e in entries if e.channel.endswith("CustomerAddedEvent")
            and e.type == EntryPointType.EVENT_LISTENER]
    assert cmds, "@CommandHandler constructor param not routed"
    assert evts, "@EventHandler/@EventSourcingHandler event not routed"


def test_axon_aggregate_apply_publishes_event():
    """AggregateLifecycle.apply(new Event(...)) — the Axon event publisher."""
    src = """
    package app;
    import commons.event.OrderCreatedEvent;
    class OrderAggregate {
        @CommandHandler
        void create(CreateOrderCommand cmd) {
            AggregateLifecycle.apply(new OrderCreatedEvent(cmd.id));
        }
    }
    """
    _, _, producers = detect({"O.java": src})
    hits = [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER
            and p.channel.endswith("OrderCreatedEvent")]
    assert hits, "AggregateLifecycle.apply(new Event) not detected as a producer"


def test_axon_constructor_apply_links_cross_service():
    """The real Axon aggregate-creation idiom: a @CommandHandler CONSTRUCTOR
    applies the event; another service's @EventHandler consumes it."""
    _, _, _, links = detect_multi({
        "order": {"O.java": """
            package order;
            import commons.event.OrderCreatedEvent;
            public class OrderAggregate {
                @CommandHandler
                public OrderAggregate(CreateOrderCommand cmd) {
                    AggregateLifecycle.apply(new OrderCreatedEvent(cmd.id));
                }
            }
        """},
        "projection": {"P.java": """
            package proj;
            import commons.event.OrderCreatedEvent;
            public class OrderProjector {
                @EventHandler
                void on(OrderCreatedEvent e) {}
            }
        """},
    })
    assert any(l.channel.endswith("OrderCreatedEvent") for l in links), \
        "constructor-apply → cross-service @EventHandler link missing"


def test_axon_bare_apply_ignored():
    """A plain apply(...) call (no AggregateLifecycle receiver) is too
    generic to treat as event publication."""
    src = """
    class Svc {
        void go() { apply(new Thing()); }
        void apply(Object o) {}
    }
    class Thing {}
    """
    _, _, producers = detect({"S.java": src})
    assert not [p for p in producers if p.channel.endswith("Thing")], \
        "bare apply() must not publish"


# ── Linker: broadcast refinement (source self-consumes, peers consume) ──

def test_broadcast_source_links_pure_consumers():
    """Axon shape: the source applies an event AND projects it itself, while
    another service is a pure consumer. The self-addressing rule must NOT
    drop the edge — the publish is a real broadcast."""
    src_prod = """
    package src;
    import shared.CustomerAddedEvent;
    public class Aggregate {
        void create() { AggregateLifecycle.apply(new CustomerAddedEvent()); }
        @EventSourcingHandler
        void on(CustomerAddedEvent e) {}
    }
    """
    src_cons = """
    package proj;
    import shared.CustomerAddedEvent;
    public class Projector {
        @EventHandler
        void on(CustomerAddedEvent e) {}
    }
    """
    _, _, _, links = detect_multi({
        "source": {"A.java": src_prod},
        "projection": {"P.java": src_cons},
    })
    assert any(l.channel.endswith("CustomerAddedEvent") for l in links), \
        "broadcast source (self-consuming) must still link pure consumers"


def test_closed_redrive_loop_still_does_not_link():
    """Two repos that each both publish AND consume the same channel (a
    closed re-drive loop) must still NOT link — regression guard for the
    original self-addressing rule."""
    flow = """
    package flow.{pkg};
    import shared.UnpaidEvent;
    public class Reversal {{
        private MessageBus bus;
        public void send() {{ bus.publish(new UnpaidEvent()); }}
        @MessageHandler public static class H {{
            @Handle public void on(UnpaidEvent msg) {{}}
        }}
    }}
    """
    _, _, _, links = detect_multi({
        "payments": {"R.java": flow.format(pkg="payments")},
        "collections": {"R.java": flow.format(pkg="collections")},
    })
    assert not [l for l in links if l.channel.endswith("UnpaidEvent")], \
        "closed self-addressing loop must not cross-link"
