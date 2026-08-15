"""In-house message-bus detection (producer + consumer + link).

Estates that wrap all messaging behind a custom facade (``bus.send(payload)``
with type-based routing, ``@MessageHandler``/``@Handle`` consumers) are
invisible to the broker-specific detectors: no JmsTemplate, no @JmsListener,
and the destination lives in config keyed by payload type. This suite pins the
generic detection added for that shape:

  * producer — send/publish on a bus-looking field type, channel = payload type
    (``new P(...)``, ``P.builder()...build()`` chains, typed locals/fields)
  * consumer — @MessageHandler class + @Handle method, channel = payload type
  * cross-repo link — producer channel and consumer channel join on the type

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).
"""
from __future__ import annotations

from engine.symbol_index import SymbolIndex
from engine.entry_detector import EntryPointDetector
from engine.cross_repo import CrossRepoLinker
from engine.models import EntryPointType, ProducerType

from test_tier12_detection import detect, find


# ── producers ──────────────────────────────────────────────────────

def test_bus_send_new_payload_detected():
    src = """
    class PaymentService {
        private MessageBus bus;
        void pay() { bus.send(new CoreBankingRequestV1("x")); }
    }
    interface MessageBus { void send(Object o); }
    class CoreBankingRequestV1 { CoreBankingRequestV1(String s) {} }
    """
    _, _, producers = detect({"PaymentService.java": src})
    hits = [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]
    assert hits, "bus.send(new ...) not detected as message-bus producer"
    assert hits[0].channel == "CoreBankingRequestV1"
    assert hits[0].message_type == "CoreBankingRequestV1"


def test_bus_send_builder_chain_detected():
    src = """
    class PaymentService {
        private MessageBus bus;
        void pay() { bus.send(ProcessFeedbackV1.builder().x(1).build()); }
    }
    interface MessageBus { void send(Object o); }
    class ProcessFeedbackV1 { static Builder builder() { return null; } class Builder { Builder x(int i) { return this; } Object build() { return null; } } }
    """
    _, _, producers = detect({"PaymentService.java": src})
    hits = [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]
    assert hits, "bus.send(P.builder()...build()) not detected"
    assert hits[0].channel == "ProcessFeedbackV1"


def test_bus_send_typed_local_detected():
    src = """
    class PaymentService {
        private MessageBus bus;
        void pay() {
            ProcessFeedbackV1 cmd = ProcessFeedbackV1.builder().build();
            bus.send(cmd);
        }
    }
    interface MessageBus { void send(Object o); }
    class ProcessFeedbackV1 { static Builder builder() { return null; } class Builder { Object build() { return null; } } }
    """
    _, _, producers = detect({"PaymentService.java": src})
    hits = [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]
    assert hits, "bus.send(localTypedVar) not detected"
    assert hits[0].channel == "ProcessFeedbackV1"


def test_bus_publish_event_detected():
    src = """
    class Publisher {
        private EventBus events;
        void done() { events.publish(new OrderCompletedV1(9)); }
    }
    interface EventBus { void publish(Object o); }
    class OrderCompletedV1 { OrderCompletedV1(int i) {} }
    """
    _, _, producers = detect({"Publisher.java": src})
    hits = [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]
    assert hits and hits[0].channel == "OrderCompletedV1"


def test_bus_send_method_param_payload_detected():
    """bus.send(payload, context) where payload is an enclosing method param."""
    src = """
    package flow;
    import adapter.lib.CoreBankingRequestV1;
    class OutboundMessages {
        private MessageBus bus;
        public void sendCoreBankingRequest(CoreBankingRequestV1 req, String iso) {
            bus.send(req, MessageContext.builder().build());
        }
    }
    interface MessageBus { void send(Object o, Object ctx); }
    """
    _, _, producers = detect({"OutboundMessages.java": src})
    hits = [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]
    assert hits, "bus.send(methodParam, ctx) not detected"
    assert hits[0].channel == "adapter.lib.CoreBankingRequestV1"


def test_bus_send_generic_type_variable_ignored():
    """``bus.send(chunk)`` where chunk is a generic ``T extends Command``
    carries no concrete type key — must not emit a producer."""
    src = """
    class Throttler {
        private MessageBus bus;
        protected <T extends Command> void throttle(T chunk, ZonedDateTime at) {
            bus.send(chunk, at);
        }
    }
    interface MessageBus { void send(Object o, ZonedDateTime at); }
    class Command {}
    """
    _, _, producers = detect({"Throttler.java": src})
    assert not [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]


def test_bus_send_parameterized_wrapper_ignored():
    """``bus.send(soapEnvelope)`` where soapEnvelope is SoapEnvelopeBase<T>
    (a wrapper, not a concrete message) must not emit a producer."""
    src = """
    class Client {
        private MessageBus bus;
        protected <T> void sendJMS(SoapEnvelopeBase<T> soapEnvelope) {
            bus.send(soapEnvelope);
        }
    }
    interface MessageBus { void send(Object o); }
    class SoapEnvelopeBase<T> {}
    """
    _, _, producers = detect({"Client.java": src})
    assert not [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]


def test_non_bus_send_ignored():
    """A send() on a receiver whose field type doesn't look like a bus is skipped."""
    src = """
    class Queue<T> { void send(T t) {} }
    class Svc {
        private WidgetRepository repo;
        private java.util.ArrayDeque<String> deque;
        void run() { repo.send(new OrderV1()); }
    }
    class WidgetRepository { void send(Object o) {} }
    class OrderV1 {}
    """
    _, _, producers = detect({"Svc.java": src})
    assert not [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]


def test_bus_send_untyped_local_ignored():
    """bus.send(var) where the local's type isn't an indexed class emits nothing."""
    src = """
    class Svc {
        private MessageBus bus;
        void run() {
            var step = makeStep();
            bus.send(step);
        }
        Object makeStep() { return null; }
    }
    interface MessageBus { void send(Object o); }
    """
    _, _, producers = detect({"Svc.java": src})
    assert not [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]


def test_bus_send_byte_payload_ignored():
    """The byte[] transport overload carries no type key and must be skipped."""
    src = """
    class Svc {
        private MessageBus bus;
        void run(byte[] body, MessageContext ctx) { bus.send(body, ctx); }
    }
    interface MessageBus { void send(byte[] b, MessageContext c); void send(Object o); }
    class MessageContext {}
    """
    _, _, producers = detect({"Svc.java": src})
    assert not [p for p in producers if p.type == ProducerType.MESSAGE_BUS_PRODUCER]


# ── consumers ──────────────────────────────────────────────────────

def test_message_handler_entry_detected():
    src = """
    package za.co.sbg.tag.flow.payments.app;
    @MessageHandler
    class InstructionHandler {
        @Handle
        public void handle(ProcessPaymentInstructionV1 cmd) {}
    }
    class ProcessPaymentInstructionV1 {}
    """
    _, entries, _ = detect({"InstructionHandler.java": src})
    hits = [e for e in entries if e.type == EntryPointType.MESSAGE_HANDLER]
    assert hits, "@MessageHandler + @Handle(payload) not detected"
    # FQN-keyed: same-package resolution applies even though the class is local
    assert hits[0].channel.endswith("ProcessPaymentInstructionV1")
    assert hits[0].message_type == hits[0].channel


def test_message_handler_envelope_generic_unwrapped():
    src = """
    package flow;
    @MessageHandler
    class TransactionResponseHandler {
        @Handle
        public void handle(ContextualizedMessage<CoreBankingResponseV1> msg) {}
    }
    """
    _, entries, _ = detect({"T.java": src})
    hits = [e for e in entries if e.type == EntryPointType.MESSAGE_HANDLER]
    assert hits, "envelope-wrapped @Handle not detected"
    assert hits[0].channel == "flow.CoreBankingResponseV1"


def test_handler_method_without_class_marker_ignored():
    src = """
    class Plain {
        @Handle
        public void handle(ProcessPaymentInstructionV1 cmd) {}
    }
    class ProcessPaymentInstructionV1 {}
    """
    _, entries, _ = detect({"Plain.java": src})
    assert not find(entries, EntryPointType.MESSAGE_HANDLER)


def test_handler_class_without_annotated_methods_ignored():
    src = """
    @MessageHandler
    class OnlyMarker {
        public void handle(ProcessPaymentInstructionV1 cmd) {}
    }
    class ProcessPaymentInstructionV1 {}
    """
    _, entries, _ = detect({"OnlyMarker.java": src})
    assert not find(entries, EntryPointType.MESSAGE_HANDLER)


# ── the link ───────────────────────────────────────────────────────

def test_bus_producer_consumer_cross_repo_link():
    """Producer in one repo, @Handle consumer in another → one message link.

    The payload type is a SHARED library class not present in either repo —
    both import it, and the FQN key matches across the import boundary.
    """
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pub = root / "Publisher.java"
        pub.write_text("""
        package adapter;
        import shared.lib.CoreBankingRequestV1;
        public class Publisher {
            private MessageBus bus;
            public void forward() { bus.send(new CoreBankingRequestV1()); }
        }
        interface MessageBus { void send(Object o); }
        """)
        hdl = root / "Handler.java"
        hdl.write_text("""
        package flow;
        import shared.lib.CoreBankingRequestV1;
        public class CoreBankingHandler {
            @MessageHandler public static class H {
                @Handle public void onCmd(CoreBankingRequestV1 cmd) {}
            }
        }
        """)
        idx = SymbolIndex()
        idx.build([("adapter", root, pub), ("flow", root, hdl)])
        entries, producers = EntryPointDetector(idx).scan()

    links = CrossRepoLinker().link(entries, producers)
    msg = [l for l in links if l.kind == "message" and l.channel == "shared.lib.CoreBankingRequestV1"]
    assert msg, (
        f"expected shared.lib.CoreBankingRequestV1 message link, got channels "
        f"{[l.channel for l in links]}"
    )
    repos_p = {pid.split(':')[0] for pid in msg[0].producers}
    repos_c = {cid.split(':')[0] for cid in msg[0].consumers}
    assert repos_p == {"adapter"} and repos_c == {"flow"}


def test_self_addressing_repo_does_not_link_peers():
    """A repo that both publishes AND consumes a channel (its own re-drive)
    must not create a cross-repo edge to another repo that also self-consumes
    it. Only external producers form the edge."""
    import tempfile
    from pathlib import Path
    FLOW = """
    package flow.{pkg};
    import shared.lib.CoreBankingUnpaidResponseV1;
    public class ReversalService {{
        private MessageBus bus;
        public void send() {{ bus.publish(new CoreBankingUnpaidResponseV1()); }}
        @MessageHandler public static class H {{
            @Handle public void on(UnpaidResponse msg) {{}}
        }}
    }}
    class UnpaidResponse {{}}
    interface MessageBus {{ void publish(Object o); }}
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for pkg in ("payments", "collections"):
            d = root / pkg
            d.mkdir(parents=True, exist_ok=True)
            (d / "ReversalService.java").write_text(FLOW.format(pkg=pkg))
        idx = SymbolIndex()
        idx.build([
            ("payments", root, root / "payments" / "ReversalService.java"),
            ("collections", root, root / "collections" / "ReversalService.java"),
        ])
        entries, producers = EntryPointDetector(idx).scan()

    links = CrossRepoLinker().link(entries, producers)
    cross = [l for l in links if l.channel == "shared.lib.CoreBankingUnpaidResponseV1"]
    assert not cross, (
        f"self-addressing repos must not cross-link, got {[l.channel for l in cross]}"
    )


def test_external_producer_links_to_consuming_repo():
    """An external producer on a channel links to every consuming repo even
    when those consumers also self-publish."""
    import tempfile
    from pathlib import Path
    FLOW = """
    package flow.{pkg};
    import shared.lib.CoreBankingUnpaidResponseV1;
    public class ReversalService {{
        private MessageBus bus;
        public void send() {{ bus.publish(new CoreBankingUnpaidResponseV1()); }}
        @MessageHandler public static class H {{
            @Handle public void on(CoreBankingUnpaidResponseV1 msg) {{}}
        }}
    }}
    interface MessageBus {{ void publish(Object o); }}
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fin = root / "finacle"
        fin.mkdir(parents=True, exist_ok=True)
        (fin / "Finacle.java").write_text("""
        package finacle;
        import shared.lib.CoreBankingUnpaidResponseV1;
        public class Finacle {
            private MessageBus messageBus;
            void s() { messageBus.publish(new CoreBankingUnpaidResponseV1()); }
        }
        interface MessageBus { void publish(Object o); }
        """)
        files = [("finacle", root, fin / "Finacle.java")]
        for pkg in ("payments", "collections"):
            d = root / pkg
            d.mkdir(parents=True, exist_ok=True)
            p = d / "ReversalService.java"
            p.write_text(FLOW.format(pkg=pkg))
            files.append((pkg, root, p))
        idx = SymbolIndex()
        idx.build(files)
        entries, producers = EntryPointDetector(idx).scan()

    links = CrossRepoLinker().link(entries, producers)
    ch = "shared.lib.CoreBankingUnpaidResponseV1"
    cross = [l for l in links if l.channel == ch]
    assert cross, f"expected external->consumers link, got {[l.channel for l in links]}"
    prod_repos = {pid.split(':')[0] for pid in cross[0].producers}
    cons_repos = {cid.split(':')[0] for cid in cross[0].consumers}
    # external finacle producer remains; the self-publishing flows are dropped
    assert prod_repos == {"finacle"}, f"expected only finacle to produce, got {prod_repos}"
    assert cons_repos == {"payments", "collections"}, f"got {cons_repos}"


def test_same_simple_name_different_packages_do_not_link():
    """Two repos sending same-simple-named messages in their own packages
    must NOT link — the FQN key distinguishes them."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a = root / "A.java"
        a.write_text("""
        package flow.payments;
        public class Service {
            private MessageBus bus;
            public void run() { bus.send(new ProcessFeedbackV1()); }
        }
        interface MessageBus { void send(Object o); }
        class ProcessFeedbackV1 {}
        """)
        b = root / "B.java"
        b.write_text("""
        package flow.collections;
        public class Service {
            private MessageBus bus;
            public void run() { bus.send(new ProcessFeedbackV1()); }
        }
        interface MessageBus { void send(Object o); }
        class ProcessFeedbackV1 {}
        """)
        idx = SymbolIndex()
        idx.build([("payments", root, a), ("collections", root, b)])
        entries, producers = EntryPointDetector(idx).scan()

    links = CrossRepoLinker().link(entries, producers)
    # Both producers keyed to their own package FQN — channels differ, no link.
    payments_ch = {p.channel for p in producers if p.repo == "payments"}
    collections_ch = {p.channel for p in producers if p.repo == "collections"}
    assert payments_ch and collections_ch, "expected producers in both repos"
    assert not (payments_ch & collections_ch), (
        f"FQN keying failed: shared channels {payments_ch & collections_ch}"
    )
    assert not [l for l in links if l.channel.endswith("ProcessFeedbackV1")], (
        "same-simple-name messages in different packages must not cross-link"
    )
