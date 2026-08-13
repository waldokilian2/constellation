"""Seed-repo coverage regression checks.

Runs the engine over the full Spring Boot demo family (order / fulfillment /
notification / analytics) and asserts the analytics-service exercises the
Tier 1/2 entry points and producer types, the deliberate GAP fixtures
(orphan channels + a dependency cycle), and the deliberate DEAD-CODE fixtures
(unreachable methods + a thin handler). Also guards the abstract-method
dead-code false-positive fix: a bodyless contract method is never flagged.

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).
"""
from __future__ import annotations
import subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "repos"

SPRING_REPOS = ["order-service", "fulfillment-service", "notification-service", "analytics-service"]


def _run_engine():
    repos = [str(FIXTURES / r) for r in SPRING_REPOS]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "g.json"
        subprocess.run(
            [sys.executable, "-m", "engine.constellation", *repos, "--output", str(out)],
            cwd=REPO, check=True, capture_output=True,
        )
        import json
        return json.loads(out.read_text())


# ── Tier 1/2 entry points are represented in the seed family ───────

def test_analytics_entry_point_types_detected():
    g = _run_engine()
    kinds = {e["type"] for e in g["entry_points"]}
    expected = {
        "graphql", "grpc-service", "servlet", "soap-service",
        "cloud-function", "lifecycle", "main", "sqs-consumer",
        "websocket", "jms-consumer", "event-listener",
        "kafka-consumer", "rabbitmq-consumer", "rest-endpoint",
    }
    missing = expected - kinds
    assert not missing, f"entry-point types missing from seed repos: {missing}"


def test_analytics_entry_point_channels():
    g = _run_engine()
    analytics = [e for e in g["entry_points"] if e["repo"] == "analytics-service"]
    chans = {e["channel"] for e in analytics}
    # One representative channel per Tier 1/2 kind.
    for c in ("/AnalyticsService/recordMetric", "/analytics/report",
              "metric", "recordMetric", "metricQuery", "main",
              "analytics.queue", "/metrics.query"):
        assert c in chans, f"analytics entry channel missing: {c}"
    # method_type carried for protocol entries.
    by_chan = {e["channel"]: e for e in analytics}
    assert by_chan["/AnalyticsService/recordMetric"]["method_type"] == "GRPC"
    assert by_chan["recordMetric"]["method_type"] == "Mutation"
    assert any(e["method"] == "doGet" and e["method_type"] == "GET" for e in analytics)


# ── Producer types are represented ─────────────────────────────────

def test_analytics_producer_types_detected():
    g = _run_engine()
    kinds = {p["type"] for p in g["producers"]}
    for t in ("event-publisher", "jms-producer", "pulsar-producer",
              "nats-producer", "http-call"):
        assert t in kinds, f"producer type missing from seed repos: {t}"


def test_analytics_http_clients_detected():
    """WebClient (fluent), Apache HttpComponents, and async-http-client all
    surface as http-call producers from distinct methods."""
    g = _run_engine()
    http = [p for p in g["producers"] if p["repo"] == "analytics-service" and p["type"] == "http-call"]
    methods = {p["method"] for p in http}
    assert "AnalyticsWebClient.pushSnapshot" in methods, "WebClient fluent call not detected"
    assert "ApacheHttpUploader.upload" in methods, "Apache HttpComponents call not detected"
    assert "AsyncHttpChecker.pingHealth" in methods, "async-http-client call not detected"


# ── GAP fixtures: orphan channels + a dependency cycle ─────────────

def test_orphan_producers_present():
    from engine.graph_tools import find_orphans
    g = _run_engine()
    o = find_orphans(g)
    chans = {p["channel"] for p in o["orphan_producers"]}
    # Pulsar + StreamBridge publish to channels nothing consumes.
    assert "metrics-deadletter" in chans and "metrics-out" in chans
    assert o["summary"]["orphan_producers"] >= 2


def test_orphan_consumers_present():
    from engine.graph_tools import find_orphans
    g = _run_engine()
    o = find_orphans(g)
    chans = {c["channel"] for c in o["orphan_consumers"]}
    assert "analytics.queue" in chans, "SQS consumer should be an orphan (no detectable producer)"


def test_order_analytics_cycle_present():
    from engine.graph_tools import find_cycles
    g = _run_engine()
    cycles = find_cycles(g)["cycles"]
    assert cycles, "expected at least one dependency cycle in the seed family"
    # The cycle runs through order-service <-> analytics-service.
    pair_cycles = [c for c in cycles if set(c["repos"]) == {"order-service", "analytics-service"}]
    assert pair_cycles, f"order<->analytics cycle missing; got {cycles}"


def test_new_cross_repo_links_wired():
    g = _run_engine()
    channels = {l["channel"] for l in g["cross_repo_links"]}
    # analytics-events closes the cycle; metrics-jobs wires analytics->notification.
    assert "analytics-events" in channels
    assert "metrics-jobs" in channels


# ── DEAD-CODE fixtures: unreachable methods + thin handler ─────────

def test_legacy_report_formatter_is_dead():
    g = _run_engine()
    dead = {f"{m['class_name']}.{m['method']}" for m in g["unreachable_methods"]}
    for name in ("LegacyReportFormatter.formatCsv",
                 "LegacyReportFormatter.formatJson",
                 "LegacyReportFormatter.banner"):
        assert name in dead, f"deliberate dead code not flagged: {name}"


def test_health_check_ping_is_thin():
    from engine.graph_tools import find_dead_code
    g = _run_engine()
    thin = {h["method"] for h in find_dead_code(g)["thin_handlers"] if h["repo"] == "analytics-service"}
    assert "ping" in thin, "HealthCheckController.ping should be a thin (no-op) handler"


def test_live_producer_not_flagged_dead():
    """Reachable broker producers (wired into the call tree) must NOT appear in
    the dead-code list — only the deliberate LegacyReportFormatter should."""
    g = _run_engine()
    dead = {f"{m['class_name']}.{m['method']}" for m in g["unreachable_methods"]}
    for alive in ("PulsarMetricsProducer.emitRaw", "NatsMetricsPublisher.publish",
                  "StreamBridgeProducer.send", "AnalyticsWebClient.pushSnapshot"):
        assert alive not in dead, f"reachable producer wrongly flagged dead: {alive}"


# ── Abstract-method dead-code false-positive fix ───────────────────

def test_abstract_methods_are_contracts_not_dead_code():
    """A bodyless method (abstract / interface) is a dynamically-dispatched
    contract and must never be flagged unreachable. The gRPC *ImplBase
    abstract method is the canonical case in the seed family."""
    g = _run_engine()
    implbase_dead = [m for m in g["unreachable_methods"] if "ImplBase" in m["class_name"]]
    assert not implbase_dead, f"abstract contract flagged dead: {implbase_dead}"


def test_get_method_body_none_for_abstract():
    """Direct check: the body helper returns None for a bodyless method."""
    from engine.ast_parser import ASTParser
    from engine.languages import java_ast
    src = (
        "abstract class B {\n"
        "    abstract void recordMetric(int x);\n"
        "}\n"
    )
    root = ASTParser().parse_source(src.encode())

    def find(n):
        if n.type == "method_declaration" and n.text.decode().startswith("abstract"):
            return n
        for c in n.children:
            r = find(c)
            if r:
                return r
        return None

    m_node = find(root)
    assert m_node is not None, "could not locate the abstract method in the AST"
    assert java_ast.get_method_body(m_node) is None, "abstract method must have no body"



def test_reachability_invariants_hold():
    """The expanded family still satisfies the core reachability invariants."""
    g = _run_engine()
    assert g["methods_total"] > 0
    assert len(g["unreachable_methods"]) < g["methods_total"]
    ep_ids = {ep["id"] for ep in g["entry_points"]}
    dead_ids = {m["id"] for m in g["unreachable_methods"]}
    assert not (ep_ids & dead_ids), "entry-point methods must not be flagged dead"


# ── Interface impl fan-out: reachability must reach ALL impls ──────

JEE_REPOS = ["java-ee-order-service", "java-ee-fulfillment-service", "java-ee-notification-service"]


def _run_engine_jee():
    repos = [str(FIXTURES / r) for r in JEE_REPOS]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "g.json"
        subprocess.run(
            [sys.executable, "-m", "engine.constellation", *repos, "--output", str(out)],
            cwd=REPO, check=True, capture_output=True,
        )
        import json
        return json.loads(out.read_text())


def test_interface_impls_all_reachable_spring():
    """SmsNotifier.send must NOT be flagged dead: the Notifier interface call
    from NotificationService fans out to BOTH impls (Email + SMS)."""
    g = _run_engine()
    dead = {f"{m['class_name']}.{m['method']}" for m in g["unreachable_methods"]}
    assert "SmsNotifier.send" not in dead, "SMS impl wrongly flagged dead (fan-out missing)"
    assert "EmailNotifier.send" not in dead, "Email impl wrongly flagged dead"


def test_interface_impls_all_reachable_jee():
    g = _run_engine_jee()
    dead = {f"{m['class_name']}.{m['method']}" for m in g["unreachable_methods"]}
    assert "SmsNotifier.send" not in dead, "JEE SMS impl wrongly flagged dead"


# ── Accidental gaps closed; only deliberate ones remain ────────────

def test_spring_only_deliberate_orphans_remain():
    """The wired consumers (inventory-updates, PaymentConfirmedEvent) must no
    longer be orphaned; only the deliberate SQS + broker-boundary gaps stay."""
    g = _run_engine()
    from engine.graph_tools import find_orphans
    o = find_orphans(g)
    cons = {c["channel"] for c in o["orphan_consumers"]}
    assert "inventory-updates" not in cons, "inventory-updates still orphaned"
    assert "PaymentConfirmedEvent" not in cons, "PaymentConfirmedEvent still orphaned"
    assert cons == {"analytics.queue"}, f"unexpected orphan consumers: {cons}"
    prods = {p["channel"] for p in o["orphan_producers"]}
    assert "metrics-deadletter" in prods and "metrics-out" in prods


def test_spring_new_entry_points_wired():
    """The new restock/payment entry points resolve real producer calls."""
    g = _run_engine()
    producers = {p["method"] for p in g["producers"]}
    assert "InventoryUpdateProducer.publishRestock" in producers
    assert "PaymentEventPublisher.paymentConfirmed" in producers


# ── JEE fixtures: no thin handlers, only natural dead producers ────

def test_jee_thin_handlers_zero():
    """Every JEE entry point now resolves a real call tree (no empty stubs)."""
    g = _run_engine_jee()
    from engine.graph_tools import find_dead_code
    thin = find_dead_code(g)["thin_handlers"]
    assert not thin, f"JEE entry points must not be thin: {thin}"


def test_jee_dead_code_is_intentional():
    """JEE dead code is the natural unused producer methods only — no SMS
    impl false positive, no thin handlers."""
    g = _run_engine_jee()
    dead = {f"{m['class_name']}.{m['method']}" for m in g["unreachable_methods"]}
    assert dead == {
        "OrderEventProducer.emitOrderUpdated",
        "OrderEventProducer.emitOrderCancelled",
        "ShipmentEventProducer.emitDelivered",
        "FulfillmentService.releaseShipment",
    }, f"unexpected JEE dead code: {dead}"


def test_jee_orphan_consumers_deliberate_only():
    """fulfillment-commands is now wired; only the deliberate CDI orphan
    (InventoryChanged) remains."""
    g = _run_engine_jee()
    from engine.graph_tools import find_orphans
    cons = {c["channel"] for c in find_orphans(g)["orphan_consumers"]}
    assert "fulfillment-commands" not in cons, "fulfillment-commands still orphaned"
    assert cons == {"InventoryChanged"}, f"unexpected JEE orphan consumers: {cons}"
